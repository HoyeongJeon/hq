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


def public_api():
    return {name for name in dir(Bridge) if not name.startswith("_") and callable(getattr(Bridge, name))}


def app_warnings(caplog):
    return [r for r in caplog.records if r.name == "hq.app" and r.levelno == logging.WARNING]


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
        assert set(window._functions) == public_api()
        assert not [name for name in window._functions if name.startswith("_")]
        for name, func in window._functions.items():
            assert func.__name__ == name
            assert func.__self__ is bridge
        # 화면이 부르는 이름은 모두 내놓은 것이다(window.pywebview.api.<이름>)
        called = set(re.findall(r"\bapi\.(\w+)\(", APP_JS.read_text(encoding="utf-8")))
        assert called and called <= set(window._functions)
        # 화면 스크립트가 pywebview의 호출 경로로 밑줄 속성을 불러도 닿지 않는다(js_api를 주면 닿는다)
        with caplog.at_level(logging.ERROR, logger="pywebview"):
            js_bridge_call(window, "_sessions.open", ["d" * 32, "x", ["cmd.exe", "/c", "calc"], 80, 24], "1")
        assert spawner.calls == []
        assert "does not exist" in caplog.text
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
        self.events = SimpleNamespace(closing=FakeEvent(), loaded=FakeEvent())
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
        self.settings = {"ALLOW_DOWNLOADS": True, "SHOW_DEFAULT_MENUS": True}
        self.windows = []
        self.started = []
        self._user = user  # 창이 떠 있는 동안 사용자가 하는 일. 돌아오면 창이 닫힌 것이다

    def create_window(self, title, url=None, **options):
        self.windows.append(FakeAppWindow(title, url, options))
        return self.windows[-1]

    def start(self, func=None, args=None, **options):
        self.started.append((func, args, options))
        func(*args)
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
    assert set(window.api) == public_api()
    assert window.events.loaded.handlers and window.events.closing.handlers
    assert fake.settings["ALLOW_DOWNLOADS"] is False
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
