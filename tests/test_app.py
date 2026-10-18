import ctypes
import logging
import re
import threading
import uuid
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

import pytest
import webview
from fakes import FakeSpawner, wait_for
from webview.util import js_bridge_call

from hq import app, paths
from hq.bridge import Bridge, shutdown, start_pump
from hq.inventory import Inventory
from hq.secret_store import SecretStore

INDEX = "file:///C:/hq/static/index.html"
PASSWORD = "Fake-pw-시험용-7731"
SERVER = {"name": "WEB-TEST-01", "nickname": "시험", "host": "192.0.2.10", "group": "", "username": "Administrator",
          "memo": ""}
FAST = {"poll_interval": 0.01, "drain_delay": 0.02, "close_timeout": 0.3}
APP_JS = Path(paths.resource("static/js/app.js"))
# spec 16의 표: 화면이 부를 수 있는 함수는 이것뿐이다
SPEC_API = {"list_servers", "add_server", "update_server", "set_password", "delete_server", "probe",
            "save_excel_template", "preview_excel", "apply_excel", "missing_trusted_hosts", "add_trusted_hosts",
            "server_setup_script", "get_settings", "save_settings", "open_session", "send_input", "resize",
            "reconnect", "close_session", "quit"}


def app_warnings(caplog):
    return [r for r in caplog.records if r.name == "hq.app" and r.levelno == logging.WARNING]


def app_errors(caplog):
    return [r for r in caplog.records if r.name == "hq.app" and r.levelno == logging.ERROR]


# ---------- 단일 실행 ----------


def test_single_instance_mutex():
    name = "Local\\hq-test-" + uuid.uuid4().hex
    first = app.acquire_single_instance(name)
    assert first is not None
    assert app.acquire_single_instance(name) is None
    app.release_single_instance(first)
    again = app.acquire_single_instance(name)
    assert again is not None
    app.release_single_instance(again)


def test_a_mutex_name_that_cannot_be_used_counts_as_running_and_is_logged(caplog):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateEventW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    name = "Local\\hq-test-" + uuid.uuid4().hex
    event = kernel32.CreateEventW(None, True, False, name)  # 같은 이름의 뮤텍스 아닌 객체
    try:
        assert app.acquire_single_instance(name) is None
    finally:
        kernel32.CloseHandle(event)
    assert len(app_warnings(caplog)) == 1


def test_bring_to_front_without_a_window_returns_false():
    assert app.bring_existing_to_front("hq-test-no-such-window-" + uuid.uuid4().hex) is False


USER32 = ctypes.WinDLL("user32", use_last_error=True)
USER32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                   wintypes.HINSTANCE, wintypes.LPVOID]
USER32.CreateWindowExW.restype = wintypes.HWND
USER32.DestroyWindow.argtypes = [wintypes.HWND]
USER32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
USER32.DefWindowProcW.restype = ctypes.c_ssize_t
USER32.RegisterClassW.argtypes = [ctypes.c_void_p]
USER32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


class HiddenWindow:
    """시험용 최상위 창. 보이지 않고, 닫을 때 지운다. class_name이 없으면 Windows 기본 STATIC 창."""

    def __init__(self, title, class_name=None):
        self.class_name = class_name
        if class_name:
            self.proc = WNDPROC(lambda hwnd, msg, wparam, lparam: USER32.DefWindowProcW(hwnd, msg, wparam, lparam))
            wc = WNDCLASSW(lpfnWndProc=ctypes.cast(self.proc, ctypes.c_void_p), lpszClassName=class_name)
            assert USER32.RegisterClassW(ctypes.byref(wc)), ctypes.get_last_error()
        self.hwnd = USER32.CreateWindowExW(0, class_name or "STATIC", title, 0, 0, 0, 0, 0, None, None, None, None)
        assert self.hwnd, ctypes.get_last_error()

    def close(self):
        USER32.DestroyWindow(self.hwnd)
        if self.class_name:
            USER32.UnregisterClassW(self.class_name, None)


def test_a_window_of_another_program_with_the_same_title_is_not_hq():
    # 'hq' 폴더를 연 탐색기 탭처럼, 제목만 같은 다른 프로그램의 창은 앞으로 가져오지 않는다
    title = "hq-test-" + uuid.uuid4().hex
    other = HiddenWindow(title)
    try:
        assert app.find_hq_window(title) is None
        assert app.bring_existing_to_front(title) is False
    finally:
        other.close()


def test_the_hq_window_is_the_winforms_form_with_that_title():
    title = "hq-test-" + uuid.uuid4().hex
    other = HiddenWindow(title)
    form = HiddenWindow(title, class_name="WindowsForms10.Window.8.app.0.hqtest_" + uuid.uuid4().hex[:8])
    try:
        assert app.find_hq_window(title) == form.hwnd
    finally:
        form.close()
        other.close()


# ---------- 기록 ----------


def test_logging_goes_to_a_rotating_file(tmp_path):
    handler = app.configure_logging(tmp_path / "logs")
    try:
        logging.getLogger("hq.test").info("시작")
        handler.flush()
        assert "시작" in (tmp_path / "logs" / "hq.log").read_text(encoding="utf-8")
        assert handler.maxBytes == 1_000_000
        assert handler.backupCount == 2
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()


# ---------- 창 ----------


class FakeWindow:
    def __init__(self, result=None, url="file:///C:/hq/static/index.html"):
        self.result = result
        self.url = url
        self.loaded = []
        self.calls = []

    def create_file_dialog(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result

    def get_current_url(self):
        return self.url

    def load_url(self, url):
        self.loaded.append(url)


def test_dialogs_return_one_path_or_none():
    assert app.WindowDialogs(lambda: FakeWindow(("C:\\a.xlsx",))).open_xlsx() == "C:\\a.xlsx"
    assert app.WindowDialogs(lambda: FakeWindow(None)).open_xlsx() is None
    assert app.WindowDialogs(lambda: FakeWindow("C:\\b.xlsx")).save_xlsx("x.xlsx") == "C:\\b.xlsx"
    window = FakeWindow(("C:\\c.xlsx",))
    assert app.WindowDialogs(lambda: window).save_xlsx("양식.xlsx") == "C:\\c.xlsx"
    assert window.calls[0][1]["save_filename"] == "양식.xlsx"


def test_window_returns_when_it_wanders_to_a_web_address():
    index = "file:///C:/hq/static/index.html"
    window = FakeWindow(url="https://example.com/")
    app.stay_on_index(window, index)
    assert window.loaded == [index]
    window = FakeWindow(url=index)
    app.stay_on_index(window, index)
    assert window.loaded == []


@pytest.mark.parametrize("url", [
    "http://192.0.2.1/",
    "https://example.com/",
    "file:///C:/Users/me/Downloads/dropped.html",  # 창에 끌어다 놓은 파일
    "file:///C:/hq/static/other.html",
    "file:///C:/hq/static/index.html?x=1",
    "about:blank",
    "",
    None,
])
def test_the_window_goes_back_to_the_hq_page_from_anywhere_else(url, caplog):
    window = FakeWindow(url=url)
    app.stay_on_index(window, INDEX)
    assert window.loaded == [INDEX]
    assert len(app_warnings(caplog)) == 1


KOREAN_INDEX = Path("C:/Users/전호영/AppData/Local/Temp/_MEI12/hq/static/index.html").as_uri()


@pytest.mark.parametrize(("current", "index"), [
    (INDEX, INDEX),
    (INDEX + "#servers", INDEX),
    ("file:///c:/HQ/static/index.html", INDEX),  # Windows 경로는 대소문자를 가리지 않는다
    # onefile exe는 사용자 폴더 아래 임시 폴더에서 돈다. as_uri()는 한글을 %인코딩하고 WebView2는 풀어서 알려 준다
    ("file:///C:/Users/전호영/AppData/Local/Temp/_MEI12/hq/static/index.html", KOREAN_INDEX),
    (KOREAN_INDEX, KOREAN_INDEX),
    ("file:///C:/my apps/hq/static/index.html", Path("C:/my apps/hq/static/index.html").as_uri()),
])
def test_the_hq_page_itself_is_left_alone(current, index, caplog):
    window = FakeWindow(url=current)
    app.stay_on_index(window, index)
    assert window.loaded == []
    assert app_warnings(caplog) == []


def test_a_failing_url_check_is_logged_as_a_warning(caplog):
    class GoneWindow(FakeWindow):
        def get_current_url(self):
            raise RuntimeError("창이 없음")

    app.stay_on_index(GoneWindow(), INDEX)
    (warning,) = app_warnings(caplog)
    assert warning.exc_info is not None


def test_an_error_while_asking_to_close_is_logged_and_lets_the_window_close(monkeypatch, caplog):
    def broken(bridge):
        raise RuntimeError("고장")

    monkeypatch.setattr(app, "close_requested", broken)
    assert app.allow_close(object()) is True
    (warning,) = app_warnings(caplog)
    assert warning.exc_info is not None


def test_a_window_that_cannot_be_destroyed_is_logged_as_a_warning(caplog):
    class StuckWindow:
        def destroy(self):
            raise RuntimeError("안 닫힘")

    app.close_window(StuckWindow())
    (warning,) = app_warnings(caplog)
    assert warning.exc_info is not None


# ---------- 화면에 내놓는 함수 ----------


def make_bridge(tmp_path, spawner, **options):
    inventory = Inventory(tmp_path / "inventory.json")
    inventory.load()
    return Bridge(inventory=inventory, secrets=SecretStore(tmp_path / "secrets"), emit=lambda script: None,
                  dialogs=app.WindowDialogs(lambda: None), destroy=lambda: None, spawn=spawner,
                  probe=lambda hosts: dict.fromkeys(hosts, True), read_trusted=lambda: ["*"],
                  session_options=FAST, **options)


def test_only_the_screen_api_is_exposed_and_private_parts_stay_out_of_reach(tmp_path, caplog):
    spawner = FakeSpawner()
    bridge = make_bridge(tmp_path, spawner)
    window = webview.Window("hq-test", "HQ", INDEX)  # pywebview 6.2.1의 진짜 창 객체(GUI는 띄우지 않는다)
    try:
        app.expose_api(window, bridge)
        # 내놓은 것 = spec 16의 표 = 화면이 부르는 것(window.pywebview.api.<이름>)
        called = set(re.findall(r"\bapi\.(\w+)\(", APP_JS.read_text(encoding="utf-8")))
        assert set(window._functions) == SPEC_API == called
        for name, func in window._functions.items():
            assert func.__name__ == name
            assert func.__self__ is bridge
        # 화면 스크립트가 pywebview의 호출 경로로 밑줄 속성을 불러도 닿지 않는다(js_api를 주면 닿는다)
        with caplog.at_level(logging.ERROR, logger="pywebview"):
            js_bridge_call(window, "_sessions.open", ["d" * 32, "x", ["cmd.exe", "/c", "calc"], 80, 24], "1")
        assert spawner.calls == []
        assert "does not exist" in caplog.text
    finally:
        shutdown(bridge)


# ---------- hq 화면 지키기 (spec 8.2) ----------


class FakeControl:
    """WinForms WebView2 컨트롤에서 PageGuard가 쓰는 것만."""

    def __init__(self):
        self.AllowExternalDrop = True
        self.NavigationStarting = FakeEvent()

    def navigate(self, uri):
        """WebView2처럼 이동을 시작하기 전에 NavigationStarting을 부른다. 이동하면 True."""
        args = SimpleNamespace(Uri=uri, Cancel=False)
        for handler in self.NavigationStarting.handlers:
            handler(self, args)
        return not args.Cancel


def guarded_window(renderer="edgechromium"):
    return SimpleNamespace(native=SimpleNamespace(webview=FakeControl()), gui=SimpleNamespace(renderer=renderer))


def test_the_window_refuses_drops_and_cancels_navigation_away_before_it_starts(caplog):
    window = guarded_window()
    app.PageGuard(INDEX, bridge=None).before_show(window)
    control = window.native.webview
    assert control.AllowExternalDrop is False  # 창에 끌어다 놓은 파일·링크로 페이지가 바뀌지 않는다
    assert app_errors(caplog) == []
    for uri in ["http://192.0.2.1/", "https://example.com/", "about:blank", "data:text/html,x",
                "file:///C:/Users/me/Downloads/dropped.html", "file:///C:/hq/static/other.html"]:
        assert control.navigate(uri) is False, uri
    assert len(app_warnings(caplog)) == 6
    for uri in [INDEX, INDEX + "#servers", "file:///c:/HQ/static/index.html"]:
        assert control.navigate(uri) is True, uri


def test_a_navigation_that_cannot_be_checked_is_cancelled(caplog):
    window = guarded_window()
    app.PageGuard(INDEX, bridge=None).before_show(window)

    class BrokenArgs:
        Cancel = False

        @property
        def Uri(self):  # noqa: N802 (.NET 속성 이름)
            raise RuntimeError("주소 없음")

    args = BrokenArgs()
    (handler,) = window.native.webview.NavigationStarting.handlers
    handler(window.native.webview, args)
    assert args.Cancel is True
    (warning,) = app_warnings(caplog)
    assert warning.exc_info is not None


def test_a_renderer_other_than_webview2_is_logged_as_an_error(caplog):
    # pywebview는 WebView2를 쓰지 못하면 말없이 MSHTML(IE)로 띄운다
    app.PageGuard(INDEX, bridge=None).before_show(guarded_window(renderer="mshtml"))
    (error,) = app_errors(caplog)
    assert "mshtml" in error.getMessage()


def test_a_window_that_cannot_be_guarded_is_logged_as_an_error(caplog):
    app.PageGuard(INDEX, bridge=None).before_show(SimpleNamespace(native=None, gui=SimpleNamespace(
        renderer="edgechromium")))
    (error,) = app_errors(caplog)
    assert error.exc_info is not None


class PageWindow(FakeWindow):
    """문서가 있는 창. href는 문서의 실제 주소(location.href), url은 pywebview가 아는 주소(get_current_url).
    문서마다 JS 전역 변수가 새로 생긴다. reload()는 문서를 새로 읽는다(전역 변수가 사라진다). load_url은 기록만 한다."""

    def __init__(self, url=INDEX, href=None):
        super().__init__(url=url)
        self.href = href or url
        self.js_globals = {}

    def evaluate_js(self, script):
        match = re.fullmatch(r"\[location\.href, window\.(\w+) \|\| \(window\.\1 = '(\w+)'\)\]", script)
        assert match, script
        return [self.href, self.js_globals.setdefault(match.group(1), match.group(2))]

    def reload(self):
        self.js_globals = {}


def test_reverting_stops_when_it_keeps_happening_and_says_so_once(caplog):
    # 주소 비교가 어긋나면(예: 폴더 이름의 #을 풀어서 알려 줌) 되돌린 hq 화면도 남의 주소로 보여 끝없이 되돌린다
    now = [0.0]
    guard = app.PageGuard(INDEX, bridge=None, clock=lambda: now[0])
    window = PageWindow(url="file:///C:/hq/static/elsewhere.html")  # 되돌려도 주소가 그대로다
    for _ in range(10):
        guard.on_loaded(window)
        now[0] += 0.5
    assert window.loaded == [INDEX] * app.REVERT_LIMIT
    assert len(app_errors(caplog)) == 1


def test_occasional_reverts_keep_working():
    now = [0.0]
    guard = app.PageGuard(INDEX, bridge=None, clock=lambda: now[0])
    window = PageWindow(url="http://192.0.2.1/")
    for _ in range(app.REVERT_LIMIT * 2):
        guard.on_loaded(window)
        now[0] += app.REVERT_WINDOW
    assert window.loaded == [INDEX] * (app.REVERT_LIMIT * 2)


def test_a_refused_new_window_request_does_not_reload_the_page(tmp_path):
    # pywebview는 새 창 요청(window.open)을 컨트롤의 Source로 넘긴다. 이동을 막아도 pywebview가 아는 주소는 막힌
    # 주소로 남는다. 그것만 믿으면 멀쩡한 hq 화면을 다시 읽어 열린 탭을 잃는다(실제 창에서 확인). 문서의 주소를 본다
    spawner = FakeSpawner()
    bridge = make_bridge(tmp_path, spawner)
    try:
        server = bridge.add_server(dict(SERVER), PASSWORD)["server"]
        assert bridge.open_session("e" * 32, server["id"], 80, 24) == {"ok": True}
        guard = app.PageGuard(INDEX, bridge)
        window = PageWindow()
        guard.on_loaded(window)
        window.url = "http://127.0.0.1:9/"  # 막힌 새 창 요청 뒤: 문서(href)는 그대로 hq 화면이다
        guard.on_loaded(window)  # 막힌 이동 뒤 API를 다시 심으며 부른 loaded
        assert window.loaded == []
        assert not spawner.ptys[0].closed
    finally:
        shutdown(bridge)


@pytest.mark.parametrize("href", ["chrome-error://chromewebdata/", "about:blank"])
def test_a_foreign_document_under_the_hq_address_is_reported_not_reloaded(href, caplog):
    # 컨트롤의 Source(get_current_url)가 이미 hq 화면이면 load_url은 이동하지 않는데, pywebview는 loaded와 준비 표시를
    # 지운다. 그 뒤의 evaluate_js(화면 출력, API 반환)는 모두 20초 뒤 실패한다(실제 창에서 확인). 예: hq 화면을 읽지
    # 못해 뜬 오류 페이지, 취소된 첫 이동
    window = PageWindow(url=INDEX, href=href)
    app.PageGuard(INDEX, bridge=None).on_loaded(window)
    assert window.loaded == []
    (error,) = app_errors(caplog)
    assert href in error.getMessage()


def test_without_the_document_address_the_guard_uses_the_window_address(caplog):
    window = FakeWindow(url="http://192.0.2.1/")  # evaluate_js가 없다: 문서를 읽지 못한다
    app.PageGuard(INDEX, bridge=None).on_loaded(window)
    assert window.loaded == [INDEX]


def test_a_second_load_of_the_hq_page_closes_every_session(tmp_path, caplog):
    spawner = FakeSpawner()
    bridge = make_bridge(tmp_path, spawner)
    try:
        server = bridge.add_server(dict(SERVER), PASSWORD)["server"]
        assert bridge.open_session("e" * 32, server["id"], 80, 24) == {"ok": True}
        guard = app.PageGuard(INDEX, bridge)
        window = PageWindow()
        guard.on_loaded(window)  # 처음 읽힌 hq 화면
        guard.on_loaded(window)  # 막힌 이동 뒤 pywebview가 같은 문서에 API를 다시 심었다
        assert not spawner.ptys[0].closed
        assert app_warnings(caplog) == []
        window.reload()  # 그래도 화면이 새로 읽혔다: 탭은 사라졌고 세션은 보이지 않게 남는다
        guard.on_loaded(window)
        assert spawner.ptys[0].closed
        (warning,) = app_warnings(caplog)
        assert "1" in warning.getMessage()
        # 끝내는 것이 아니다: 새 화면에서 다시 열 수 있다
        assert bridge.open_session("f" * 32, server["id"], 80, 24) == {"ok": True}
    finally:
        shutdown(bridge)


# ---------- 실행부터 끝까지(가짜 pywebview) ----------


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def set(self):
        """pywebview처럼 처리기를 모두 부르고, 하나라도 False를 돌려주면 True(닫기 취소)."""
        results = [handler() for handler in self.handlers]
        return any(result is False for result in results)


class FakeAppWindow:
    def __init__(self, title, url, options):
        self.title = title
        self.url = url
        self.options = options
        self.events = SimpleNamespace(closing=FakeEvent(), loaded=FakeEvent(), before_show=FakeEvent())
        self.native = SimpleNamespace(webview=FakeControl())  # pywebview가 창을 만들며 채운다
        self.gui = SimpleNamespace(renderer="edgechromium")
        self.api = {}
        self.scripts = []
        self.destroyed = False

    def expose(self, *functions):
        for func in functions:
            self.api[func.__name__] = func

    def evaluate_js(self, script):
        self.scripts.append(script)

    def destroy(self):
        if not self.events.closing.set():  # pywebview도 닫기 전에 closing 이벤트를 부른다
            self.destroyed = True

    def get_current_url(self):
        return self.url

    def load_url(self, url):
        self.url = url


class FakeWebview:
    FileDialog = webview.FileDialog

    def __init__(self, user=None):
        self.settings = {"ALLOW_DOWNLOADS": True, "SHOW_DEFAULT_MENUS": True, "OPEN_EXTERNAL_LINKS_IN_BROWSER": True}
        self.windows = []
        self.started = []
        self._user = user  # 창이 떠 있는 동안 사용자가 하는 일. 돌아오면 창이 닫힌 것이다

    def create_window(self, title, url=None, **options):
        self.windows.append(FakeAppWindow(title, url, options))
        return self.windows[-1]

    def start(self, func=None, args=None, **options):
        self.started.append((func, args, options))
        func(*args)
        self.windows[0].events.before_show.set()  # pywebview: 창을 만든 뒤 보이기 전에 GUI 스레드에서
        if self._user:
            self._user(self.windows[0])


@pytest.fixture
def launch(tmp_path, monkeypatch):
    """LOCALAPPDATA를 tmp_path로, 뮤텍스 이름을 시험용으로 바꾸고 가짜 pywebview로 main()을 돌린다."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(app, "MUTEX_NAME", "Local\\hq-test-" + uuid.uuid4().hex)
    spawner = FakeSpawner()
    made = []

    def fake_bridge(**options):  # 진짜 Bridge에 가짜 프로세스·확인만 끼운다. 어디에도 접속하지 않는다
        made.append(Bridge(**options, spawn=spawner, probe=lambda hosts: dict.fromkeys(hosts, True),
                           read_trusted=lambda: ["*"], session_options=FAST))
        return made[-1]

    monkeypatch.setattr(app, "Bridge", fake_bridge)

    def run(fake):
        monkeypatch.setattr(app, "webview", fake)
        app.main()
        return fake

    run.spawner = spawner
    run.bridges = made
    run.log = lambda: (tmp_path / "hq" / "logs" / "hq.log").read_text(encoding="utf-8")
    yield run
    for bridge in made:
        shutdown(bridge)


def pump_running():
    return any(thread.name == "hq-pump" for thread in threading.enumerate())


def test_main_opens_only_the_hq_page_and_shuts_down_after_the_window_closes(launch):
    seen = {}

    def user(window):
        seen["server"] = window.api["add_server"](dict(SERVER), PASSWORD)["server"]
        seen["pump"] = wait_for(pump_running)
        seen["cancelled"] = window.events.closing.set()  # 열린 탭이 없으면 확인 없이 닫힌다

    fake = launch(FakeWebview(user))
    (window,) = fake.windows
    assert window.title == "HQ"
    assert window.url == paths.index_url()
    assert window.url.startswith("file:///")  # pywebview가 내부 웹 서버를 띄우지 않는다
    assert "js_api" not in window.options  # 화면 API는 expose로만 내놓는다
    assert set(window.api) == SPEC_API
    assert window.events.loaded.handlers and window.events.closing.handlers
    # 보이기 전에 끌어다 놓기를 끄고 이동 막기를 걸었다
    control = window.native.webview
    assert control.AllowExternalDrop is False
    assert control.navigate("about:blank") is False
    assert control.navigate(paths.index_url()) is True
    assert fake.settings["ALLOW_DOWNLOADS"] is False
    # 새 창 요청(window.open, target=_blank)은 기본 브라우저로 넘기지 않고 같은 창의 이동으로 받아 막는다
    assert fake.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] is False
    ((func, args, options),) = fake.started
    assert func is start_pump
    assert args == (launch.bridges[0],)
    assert options["debug"] is False
    assert options["private_mode"] is True
    assert not options.get("http_server")
    assert Path(options["icon"]) == paths.resource("assets/hq.ico")
    assert seen["pump"] and seen["cancelled"] is False
    # 창이 닫힌 뒤 main이 shutdown을 불렀다: 펌프가 멈추고 새 탭을 받지 않는다
    assert wait_for(lambda: not pump_running())
    refused = window.api["open_session"]("b" * 32, seen["server"]["id"], 80, 24)
    assert refused == {"ok": False, "error": "HQ를 끝내는 중입니다."}
    assert launch.spawner.calls == []
    # 뮤텍스를 놓았다: 다음 실행이 뜬다
    handle = app.acquire_single_instance(app.MUTEX_NAME)
    assert handle is not None
    app.release_single_instance(handle)
    text = launch.log()
    assert "HQ 시작" in text and "HQ 끝" in text
    assert PASSWORD not in text
    assert not [h for h in logging.getLogger().handlers if getattr(h, "baseFilename", "").endswith("hq.log")]


def test_closing_with_an_open_tab_asks_the_page_and_quit_closes_everything(launch):
    seen = {}

    def user(window):
        server = window.api["add_server"](dict(SERVER), PASSWORD)["server"]
        seen["opened"] = window.api["open_session"]("a" * 32, server["id"], 80, 24)
        seen["cancelled"] = window.events.closing.set()  # 창의 X
        seen["asked"] = wait_for(lambda: "window.hq.onCloseRequested(1)" in window.scripts)
        window.api["quit"]()  # 화면의 "열린 탭 1개를 닫고 끝낼까요?"에서 [끝내기]

    fake = launch(FakeWebview(user))
    assert seen == {"opened": {"ok": True}, "cancelled": True, "asked": True}
    assert fake.windows[0].destroyed
    (pty,) = launch.spawner.ptys
    assert pty.closed
    assert wait_for(lambda: not pump_running())
    assert PASSWORD not in launch.log()


def test_pressing_x_again_while_quitting_still_waits_for_the_tabs_to_close(launch):
    # quit이 탭을 닫는 동안(최대 2초) X를 또 누르면 창은 바로 닫힌다. 그래도 main은 탭이 다 닫힐 때까지 기다린다.
    # 먼저 끝나 버리면 Ctrl+C·exit를 보내던 중에 프로세스가 사라져 서버 세션이 유휴 시간까지 남는다
    launch.spawner._exit_on_exit = False  # exit에 끝나지 않는 셸: 닫기가 close_timeout까지 걸린다
    seen = {}

    def user(window):
        server = window.api["add_server"](dict(SERVER), PASSWORD)["server"]
        window.api["open_session"]("a" * 32, server["id"], 80, 24)
        seen["quit"] = threading.Thread(target=window.api["quit"])
        seen["quit"].start()
        seen["closing"] = wait_for(lambda: "\x03" in launch.spawner.ptys[0].written)
        seen["cancelled"] = window.events.closing.set()  # X를 한 번 더

    launch(FakeWebview(user))
    (pty,) = launch.spawner.ptys
    finished = pty.closed  # main이 돌아온 그 순간에 탭이 이미 다 닫혀 있어야 한다
    seen["quit"].join(5)
    assert seen["closing"] and seen["cancelled"] is False
    assert finished and pty.terminated


def test_an_uncaught_error_in_any_thread_reaches_the_log(launch, monkeypatch):
    # 콘솔 없는 exe에는 stderr가 없다. pywebview의 호출 스레드가 죽어도(예: 화면이 이동한 사이 돌아온 값) 기록에 남긴다
    def before(args):
        raise AssertionError("main이 끝난 뒤에는 원래 처리기로 돌아간다")

    monkeypatch.setattr(threading, "excepthook", before)

    def user(window):
        def crash():
            raise RuntimeError("스레드 고장")

        thread = threading.Thread(target=crash, name="pywebview-call")
        thread.start()
        thread.join()

    launch(FakeWebview(user))
    text = launch.log()
    assert "WARNING" in text and "pywebview-call" in text and "스레드 고장" in text and "Traceback" in text
    assert threading.excepthook is before


def test_a_window_that_fails_to_start_is_logged_and_still_shuts_down(launch):
    def user(window):
        raise RuntimeError("WebView2 런타임 없음")

    with pytest.raises(RuntimeError):
        launch(FakeWebview(user))
    assert wait_for(lambda: not pump_running())
    handle = app.acquire_single_instance(app.MUTEX_NAME)
    assert handle is not None
    app.release_single_instance(handle)
    text = launch.log()
    assert "WebView2 런타임 없음" in text and "Traceback" in text
    assert "HQ 끝" in text


def test_a_second_launch_brings_the_first_window_forward_and_opens_nothing(launch, monkeypatch):
    brought = []
    monkeypatch.setattr(app, "bring_existing_to_front", lambda title: brought.append(title) or True)
    first = app.acquire_single_instance(app.MUTEX_NAME)  # 먼저 떠 있는 HQ
    try:
        fake = launch(FakeWebview())
    finally:
        app.release_single_instance(first)
    assert fake.windows == []
    assert fake.started == []
    assert launch.bridges == []
    assert brought == ["HQ"]
    assert "이미 실행 중" in launch.log()
