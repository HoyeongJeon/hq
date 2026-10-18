"""HQ 실행: 단일 실행 확인, 로그, 창 만들기, 닫기 처리."""

import ctypes
import logging
import logging.handlers
import os
import threading
import time
import uuid
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import webview

from hq import paths
from hq.bridge import Bridge, close_all_sessions, close_requested, shutdown, start_pump
from hq.inventory import Inventory, InventoryError
from hq.secret_store import SecretStore

WINDOW_TITLE = "HQ"
MUTEX_NAME = "Local\\hq-single-instance"
XLSX_FILTER = ("Excel 파일 (*.xlsx)",)
REVERT_LIMIT = 3  # REVERT_WINDOW초 안에 이보다 많이 되돌리면 되돌리기를 멈춘다(끝없는 반복과 기록 넘침을 막는다)
REVERT_WINDOW = 10.0
_DOCUMENT_MARK = "__hqDocument"  # PageGuard가 hq 화면 문서마다 남기는 JS 전역 변수
log = logging.getLogger(__name__)

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
_user32.FindWindowExW.restype = wintypes.HWND
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_ERROR_ALREADY_EXISTS = 183
_SW_RESTORE = 9
_SW_SHOW = 5
_FORM_CLASS = "WindowsForms10.Window."  # pywebview의 Windows 창은 WinForms Form이다


def acquire_single_instance(name: str = MUTEX_NAME) -> int | None:
    """이미 실행 중이면 None. 아니면 프로세스가 끝날 때까지 쥐고 있을 뮤텍스 핸들."""
    ctypes.set_last_error(0)
    handle = _kernel32.CreateMutexW(None, False, name)
    error = ctypes.get_last_error()
    if not handle:
        # 같은 이름의 다른 객체가 있거나 열 권한이 없다(관리자 권한으로 뜬 HQ가 쥐고 있을 때). 새로 띄우지 않는다
        log.warning("단일 실행 뮤텍스를 열지 못함(Windows 오류 %d)", error)
        return None
    if error == _ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)
        return None
    return handle


def release_single_instance(handle: int) -> None:
    _kernel32.CloseHandle(handle)


def find_hq_window(title: str = WINDOW_TITLE) -> int | None:
    """제목이 title인 WinForms 창(HQ 창). 제목만 같은 다른 창은 건너뛴다.

    FindWindow는 제목을 대소문자 없이 비교하고 프로그램을 가리지 않는다. 'hq' 폴더를 연 탐색기 탭은 제목이 HQ인
    최상위 창(Windows.Internal.Shell.TabProxyWindow)을 두므로 제목만으로 찾으면 탐색기를 앞으로 가져온다.
    """
    hwnd = None
    while hwnd := _user32.FindWindowExW(None, hwnd, None, title):
        name = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, name, len(name))
        if name.value.startswith(_FORM_CLASS):
            return hwnd
    return None


def bring_existing_to_front(title: str = WINDOW_TITLE) -> bool:
    hwnd = find_hq_window(title)
    if not hwnd:
        return False
    _user32.ShowWindow(hwnd, _SW_RESTORE if _user32.IsIconic(hwnd) else _SW_SHOW)
    _user32.SetForegroundWindow(hwnd)
    return True


def configure_logging(log_dir: Path) -> logging.handlers.RotatingFileHandler:
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "hq.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return handler


def _log_thread_exception(args: threading.ExceptHookArgs) -> None:
    """어느 스레드에서든 잡히지 않은 예외를 hq.log에 남긴다. 콘솔 없는 exe에는 stderr가 없어 그대로 사라진다.

    예: 화면이 다른 주소로 이동한 사이 pywebview의 호출 스레드가 돌려줄 곳을 잃고 죽는다.
    """
    name = args.thread.name if args.thread else "?"
    log.warning("스레드 %s에서 처리하지 못한 오류", name, exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


class WindowDialogs:
    """pywebview 창의 Windows 파일 대화 상자. bridge.Dialogs를 구현한다."""

    def __init__(self, get_window: Callable[[], Any]) -> None:
        self._get_window = get_window

    @staticmethod
    def _first(result: Any) -> str | None:
        if not result:
            return None
        return result if isinstance(result, str) else result[0]

    def open_xlsx(self) -> str | None:
        window = self._get_window()
        return self._first(window.create_file_dialog(webview.FileDialog.OPEN, file_types=XLSX_FILTER))

    def save_xlsx(self, default_name: str) -> str | None:
        window = self._get_window()
        result = window.create_file_dialog(webview.FileDialog.SAVE, save_filename=default_name, file_types=XLSX_FILTER)
        return self._first(result)


def _page(url: str) -> tuple[str, str, str, str]:
    """두 주소가 같은 파일을 가리키는지 비교할 모양. #조각은 빼고, 경로는 %인코딩을 풀고 대소문자를 맞춘다.

    Path.as_uri()는 한글과 공백을 %인코딩하지만 WebView2는 풀어서 알려 준다. onefile exe는 사용자 폴더(한글 이름일 수
    있다) 아래 임시 폴더에서 돌므로, 그대로 비교하면 hq 화면을 계속 다시 여는 고리에 빠진다.
    """
    parts = urlsplit(url)
    return parts.scheme.lower(), parts.netloc.lower(), os.path.normcase(unquote(parts.path)), parts.query


def stay_on_index(
    window: Any, index_url: str, may_revert: Callable[[], bool] = lambda: True, url: str | None = None
) -> bool:
    """창이 hq 화면을 보이고 있으면 True. 다른 곳(웹 주소, 다른 파일 등)이면 hq 화면으로 되돌리고 False(spec 8.2).

    pywebview는 이동한 페이지에도 화면 API를 심으므로 그대로 두지 않는다. may_revert가 False면 되돌리지 않는다.
    url은 문서의 실제 주소다. 없으면 pywebview가 아는 주소(get_current_url)를 쓴다.
    """
    try:
        source = window.get_current_url() or ""  # 컨트롤의 Source. load_url은 이것과 다를 때만 이동한다
        if url is None:
            url = source
        if _page(url) == _page(index_url):
            return True
        if _page(source) == _page(index_url):
            # load_url은 이동하지 않는데 pywebview는 loaded와 준비 표시를 지운다. 그러면 그 뒤의 evaluate_js(화면 출력,
            # API 반환)가 모두 20초 뒤 실패한다. 예: hq 화면을 읽지 못해 뜬 오류 페이지, 취소된 첫 이동
            log.error("hq 화면 주소에 다른 문서가 떠 있어 되돌리지 못함: %s", url[:200])
            return False
        if may_revert():
            log.warning("hq 화면 밖으로 이동해서 되돌림: %s", url[:200])
            window.load_url(index_url)
    except Exception:
        log.warning("창 주소를 확인하지 못함", exc_info=True)
    return False


class PageGuard:
    """창이 hq 화면만 보이게 지킨다(spec 8.2 "다른 주소로 이동하려는 시도는 막는다").

    1. before_show: 끌어다 놓기를 받지 않고(AllowExternalDrop), hq 화면이 아닌 곳으로 가는 이동은 시작하기 전에
       취소한다(NavigationStarting). 다른 문서가 한 번이라도 읽히면 그 문서의 스크립트가 화면 API를 부를 수 있다.
       pywebview는 이동이 끝나면(취소돼도) API를 다시 심고 loaded를 부른다.
    2. loaded: 그래도 다른 곳에 가 있으면 되돌린다. 짧은 시간에 거듭되면 주소 비교가 어긋난 것으로 보고 멈춘다.
       주소는 문서에서 읽는다(location.href). pywebview는 새 창 요청을 컨트롤의 Source로 넘기므로, 막은 뒤에도
       get_current_url은 막힌 주소를 가리킨다. 그것만 믿으면 멀쩡한 hq 화면을 다시 읽어 탭을 잃는다.
    3. loaded: hq 화면이 새로 읽혔으면 화면에서 사라진 탭의 세션을 모두 닫는다. 숨은 채 서버 셸을 쥔 세션을 남기지
       않는다. 같은 문서에 API만 다시 심긴 것과 가르려고 문서마다 JS 전역 변수로 표시를 남긴다.
    """

    def __init__(self, index_url: str, bridge: Bridge, clock: Callable[[], float] = time.monotonic) -> None:
        self._index_url = index_url
        self._index = _page(index_url)
        self._bridge = bridge
        self._clock = clock
        self._reverts: list[float] = []
        self._gave_up = False
        self._document: str | None = None
        self._lock = threading.Lock()  # loaded 처리기는 이벤트마다 새 스레드에서 돈다

    def before_show(self, window: Any) -> None:
        """창을 만든 뒤 보이기 전에 GUI 스레드에서(pywebview before_show 이벤트). 첫 이동보다 먼저 걸린다."""
        renderer = getattr(window.gui, "renderer", None)
        if renderer != "edgechromium":
            log.error("WebView2가 아닌 %s(으)로 창을 띄움. hq 화면이 제대로 돌지 않고 이동 막기도 걸리지 않는다",
                      renderer)
        try:
            control = window.native.webview
            control.AllowExternalDrop = False
            control.NavigationStarting += self.on_navigation_starting
        except Exception:
            log.error("창의 끌어다 놓기·이동 막기를 걸지 못함", exc_info=True)

    def on_navigation_starting(self, sender: Any, args: Any) -> None:
        """WebView2 NavigationStarting(GUI 스레드). hq 화면이 아니면, 확인하지 못해도 취소한다."""
        try:
            uri = str(args.Uri)
            if _page(uri) == self._index:
                return
            log.warning("hq 화면 밖으로 가는 이동을 막음: %s", uri[:200])
        except Exception:
            log.warning("이동할 주소를 확인하지 못해 막음", exc_info=True)
        args.Cancel = True

    def on_loaded(self, window: Any) -> None:
        with self._lock:
            href, mark = self._read_document(window)
            if stay_on_index(window, self._index_url, self._may_revert, href):
                self._check_document(mark)

    @staticmethod
    def _read_document(window: Any) -> tuple[str | None, Any]:
        """문서의 주소와 표시. 표시가 없으면 새로 남긴다. 읽지 못하면 (None, None)."""
        script = f"[location.href, window.{_DOCUMENT_MARK} || (window.{_DOCUMENT_MARK} = '{uuid.uuid4().hex}')]"
        try:
            href, mark = window.evaluate_js(script)
            return str(href), mark
        except Exception:
            log.warning("창의 문서를 읽지 못함", exc_info=True)
            return None, None

    def _may_revert(self) -> bool:
        if self._gave_up:
            return False
        now = self._clock()
        self._reverts = [t for t in self._reverts if now - t < REVERT_WINDOW] + [now]
        if len(self._reverts) <= REVERT_LIMIT:
            return True
        self._gave_up = True
        log.error("%g초 안에 %d번 넘게 되돌려 더는 되돌리지 않음(hq 화면 주소 비교가 어긋났을 수 있다)",
                  REVERT_WINDOW, REVERT_LIMIT)
        return False

    def _check_document(self, mark: Any) -> None:
        if not isinstance(mark, str):
            log.warning("hq 화면이 새로 읽혔는지 확인하지 못함: %r", mark)
            return
        first, self._document = self._document, mark
        if first is None or mark == first:
            return
        closed = close_all_sessions(self._bridge)
        log.warning("hq 화면이 다시 읽혀 보이지 않게 된 탭 %d개의 세션을 닫음", len(closed))


def allow_close(bridge: Bridge) -> bool:
    """창의 closing 이벤트. False면 닫지 않는다(열린 탭이 있어 화면에 확인을 부탁했다).

    예상하지 못한 오류는 기록하고 닫게 둔다. 창이 닫히면 main이 shutdown으로 탭을 모두 닫는다.
    """
    try:
        return close_requested(bridge)
    except Exception:
        log.warning("닫기 확인 중 오류. 그대로 닫음", exc_info=True)
        return True


def close_window(window: Any) -> None:
    """Bridge.quit이 부른다. 닫지 못했으면 기록만 한다(창의 X로는 그대로 닫을 수 있다)."""
    try:
        window.destroy()
    except Exception:
        log.warning("창을 닫지 못함", exc_info=True)


def expose_api(window: Any, bridge: Bridge) -> None:
    """화면 API(Bridge의 공개 메서드)만 bridge에 묶어 내놓는다. 화면에서는 window.pywebview.api.<이름>이다.

    js_api로 객체를 넘기지 않는다. pywebview 6.2.1은 화면이 부른 이름을 js_api 객체에서 점으로 나눈 경로로 찾고 밑줄
    이름을 막지 않아, 화면 스크립트가 _sessions.open 같은 내부를 부를 수 있다. expose한 함수는 이름이 정확히 같을 때만
    불린다.
    """
    cls = type(bridge)
    names = [name for name in dir(cls) if not name.startswith("_") and callable(getattr(cls, name))]
    window.expose(*(getattr(bridge, name) for name in names))


def main() -> None:
    """python -m hq, 바로가기, exe의 진입점."""
    data = paths.data_dir()
    handler = configure_logging(data / "logs")
    previous_hook = threading.excepthook
    threading.excepthook = _log_thread_exception
    try:
        mutex = acquire_single_instance(MUTEX_NAME)
        if mutex is None:
            found = bring_existing_to_front(WINDOW_TITLE)
            log.info("이미 실행 중인 HQ가 있어 끝냄(그 창을 %s)", "앞으로 가져옴" if found else "찾지 못함")
            return
        try:
            _run(data)
        finally:
            release_single_instance(mutex)
    finally:
        threading.excepthook = previous_hook
        logging.getLogger().removeHandler(handler)
        handler.close()


def _run(data: Path) -> None:
    inventory = Inventory(data / "inventory.json")
    load_error = None
    try:
        inventory.load()
    except InventoryError as exc:
        load_error = str(exc)
        log.error(load_error)
    index = paths.index_url()
    webview.settings["SHOW_DEFAULT_MENUS"] = False
    webview.settings["ALLOW_DOWNLOADS"] = False
    # 새 창 요청(window.open, target=_blank)을 기본 브라우저로 넘기지 않는다. 같은 창의 이동이 되어 PageGuard가 막는다
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    window = webview.create_window(
        WINDOW_TITLE, url=index, width=1280, height=820, min_size=(960, 600),
        background_color="#0d1016", text_select=True,
    )
    bridge = Bridge(
        inventory=inventory,
        secrets=SecretStore(data / "secrets"),
        emit=window.evaluate_js,  # 실패는 Bridge가 warning으로 기록한다
        dialogs=WindowDialogs(lambda: window),
        destroy=lambda: close_window(window),
        load_error=load_error,
    )
    expose_api(window, bridge)
    guard = PageGuard(index, bridge)
    window.events.before_show += lambda: guard.before_show(window)
    window.events.loaded += lambda: guard.on_loaded(window)
    window.events.closing += lambda: allow_close(bridge)
    log.info("HQ 시작")
    try:
        webview.start(start_pump, (bridge,), debug=False, private_mode=True, icon=str(paths.resource("assets/hq.ico")))
    except Exception:
        log.exception("HQ 창을 띄우지 못함")
        raise
    finally:
        shutdown(bridge)  # 탭 없이 닫혔으면 펌프가 아직 돈다. quit으로 닫혔으면 이미 했다(여러 번 불러도 된다)
        log.info("HQ 끝")
