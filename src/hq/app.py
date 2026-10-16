"""HQ 실행: 단일 실행 확인, 로그, 창 만들기, 닫기 처리."""

import ctypes
import logging
import logging.handlers
import os
import threading
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import webview

from hq import paths
from hq.bridge import Bridge, close_requested, shutdown, start_pump
from hq.inventory import Inventory, InventoryError
from hq.secret_store import SecretStore

WINDOW_TITLE = "HQ"
MUTEX_NAME = "Local\\hq-single-instance"
XLSX_FILTER = ("Excel 파일 (*.xlsx)",)
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


def stay_on_index(window: Any, index_url: str) -> None:
    """창이 hq 화면 파일이 아닌 곳(웹 주소, 끌어다 놓은 파일 등)으로 이동했으면 hq 화면으로 되돌린다(spec 8.2).

    pywebview는 이동한 페이지에도 화면 API를 심으므로 그대로 두지 않는다. 창의 loaded 이벤트에서 부른다.
    """
    try:
        url = window.get_current_url() or ""
        if _page(url) != _page(index_url):
            log.warning("hq 화면 밖으로 이동해서 되돌림: %s", url[:200])
            window.load_url(index_url)
    except Exception:
        log.warning("창 주소를 확인하지 못함", exc_info=True)


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
    window.events.closing += lambda: allow_close(bridge)
    window.events.loaded += lambda: stay_on_index(window, index)
    log.info("HQ 시작")
    try:
        webview.start(start_pump, (bridge,), debug=False, private_mode=True, icon=str(paths.resource("assets/hq.ico")))
    except Exception:
        log.exception("HQ 창을 띄우지 못함")
        raise
    finally:
        shutdown(bridge)  # 탭 없이 닫혔으면 펌프가 아직 돈다. quit으로 닫혔으면 이미 했다(여러 번 불러도 된다)
        log.info("HQ 끝")
