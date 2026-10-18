import asyncio
import json
import logging
import re
import threading
import time

import pytest
from fakes import FakeSpawner, wait_for
from openpyxl import Workbook

from hq import bridge as bridge_module
from hq.bootstrap import READY_MARKER
from hq.bridge import Bridge, close_all_sessions, close_requested, shutdown, start_pump
from hq.excel import HEADERS
from hq.inventory import Inventory
from hq.secret_store import SecretStore
from hq.trustedhosts import AddResult

SID = "c" * 32
READY = f"\x1b]0;{READY_MARKER}\x1b\\"  # 부트스트랩이 안전장치를 건 뒤 ConPTY가 내보내는 준비 신호
WEB = {"name": "WEB-PRD-01", "nickname": "웹01", "host": "192.0.2.21", "group": "운영", "username": "Administrator",
       "memo": "메모"}
API = {"list_servers", "add_server", "update_server", "set_password", "delete_server", "probe", "save_excel_template",
       "preview_excel", "apply_excel", "missing_trusted_hosts", "add_trusted_hosts", "server_setup_script",
       "get_settings", "save_settings", "open_session", "send_input", "resize", "reconnect", "close_session", "quit"}


class FakeDialogs:
    def __init__(self):
        self.open_path = None
        self.save_path = None
        self.default_name = None

    def open_xlsx(self):
        return self.open_path

    def save_xlsx(self, default_name):
        self.default_name = default_name
        return self.save_path


class Harness:
    def __init__(self, tmp_path, *, trusted=("*",), online=True, load_error=None, spawn=None, emit=None,
                 **session_options):
        self.inventory = Inventory(tmp_path / "inventory.json")
        self.inventory.load()
        self.secrets = SecretStore(tmp_path / "secrets")
        self.emitted: list[str] = []
        self.destroyed = []
        self.added = []
        self.add_result = AddResult.OK
        self.spawn = spawn or FakeSpawner()
        self.trusted = None if trusted is None else list(trusted)
        self.online = online
        self.probe = lambda hosts: {h: self.online for h in hosts}  # 테스트가 바꿔 끼운다
        self.dialogs = FakeDialogs()
        self.bridge = Bridge(
            inventory=self.inventory,
            secrets=self.secrets,
            emit=emit or self.emitted.append,
            dialogs=self.dialogs,
            destroy=lambda: self.destroyed.append(True),
            spawn=self.spawn,
            probe=lambda hosts: self.probe(hosts),
            read_trusted=lambda: self.trusted,
            add_trusted=self._add,
            load_error=load_error,
            session_options={"poll_interval": 0.01, "drain_delay": 0.02, "close_timeout": 0.3, **session_options},
        )

    def _add(self, hosts):
        self.added.append(list(hosts))
        return self.add_result

    def server(self, password="pw-1234"):
        return self.bridge.add_server(dict(WEB), password)["server"]


@pytest.fixture
def make(tmp_path):
    made = []

    def factory(**options):
        made.append(Harness(tmp_path, **options))
        return made[-1]

    yield factory
    for harness in made:
        shutdown(harness.bridge)


def outputs(emitted):
    found = []
    for script in emitted:
        match = re.fullmatch(r"window\.hq\.onOutput\((.*)\)", script, re.S)
        if match:
            found.append(json.loads(match.group(1)))
    return found


def ended(session_id, reason):
    return f'window.hq.onSessionEnded("{session_id}", "{reason}")'


def bridge_warnings(caplog):
    return [r for r in caplog.records if r.name == "hq.bridge" and r.levelno == logging.WARNING]


def write_sheet(path, *rows):
    workbook = Workbook()
    workbook.active.title = "서버"
    workbook.active.append(HEADERS)
    for row in rows:
        workbook.active.append(row)
    workbook.save(path)


class HeldProbe:
    """open_session을 사전 확인(5985 확인) 단계에 붙잡아 둔다."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def __call__(self, hosts):
        self.calls += 1
        self.entered.set()
        self.release.wait(2)
        return {h: True for h in hosts}


def open_in_background(h, server_id, session_id=SID):
    result = {}
    thread = threading.Thread(target=lambda: result.update(h.bridge.open_session(session_id, server_id, 80, 24)))
    thread.start()
    return thread, result


# ---------- 화면에 내놓는 것 ----------


def test_only_the_screen_api_is_exposed(make):
    public = {name for name in dir(Bridge) if not name.startswith("_") and callable(getattr(Bridge, name))}
    assert public == API
    # pywebview는 함수가 아닌 공개 속성 안으로도 들어가 그 메서드를 내놓는다. 인스턴스에도 화면 API 말고는 없다
    assert {name for name in dir(make().bridge) if not name.startswith("_")} == API


# ---------- 서버 목록 ----------


def test_add_and_list_never_return_passwords(make):
    h = make()
    server = h.server("Secret-pw")
    listed = h.bridge.list_servers()
    assert listed["ok"] and listed["servers"] == [server]
    assert listed["servers"][0]["has_password"] is True
    assert "Secret-pw" not in json.dumps(listed)
    assert "password" not in listed["servers"][0]


def test_add_rejects_bad_input_and_saves_nothing(make, tmp_path):
    h = make()
    assert h.bridge.add_server(dict(WEB), "")["ok"] is False
    result = h.bridge.add_server({**WEB, "host": "bad host"}, "pw")
    assert result["ok"] is False and "호스트" in result["error"]
    assert h.bridge.list_servers()["servers"] == []
    assert not (tmp_path / "secrets").exists() or not any((tmp_path / "secrets").iterdir())


def test_a_broken_inventory_blocks_the_list_and_every_change(make):
    error = "서버 목록 파일을 읽을 수 없어 저장을 막았습니다"
    h = make(load_error=error)
    h.dialogs.open_path = "C:\\없는\\파일.xlsx"
    blocked = {"ok": False, "error": error}
    assert h.bridge.list_servers() == blocked
    assert h.bridge.add_server(dict(WEB), "pw") == blocked
    assert h.bridge.update_server("a" * 32, dict(WEB)) == blocked
    assert h.bridge.delete_server("a" * 32) == blocked
    assert h.bridge.preview_excel() == blocked
    assert h.bridge.apply_excel("a" * 32, False) == blocked
    assert h.bridge.save_settings(["198.51.100.50"]) == blocked


def test_set_password_overwrites_and_rejects_empty(make):
    h = make()
    server = h.server()
    h.secrets.delete(server["id"])
    assert h.bridge.set_password(server["id"], "")["ok"] is False
    assert h.bridge.set_password("a" * 32, "pw")["ok"] is False
    assert h.bridge.set_password(server["id"], "new-pw") == {"ok": True}
    assert h.bridge.list_servers()["servers"][0]["has_password"] is True


def test_editing_a_server_leaves_its_open_tab_alone(make):
    h = make()
    server = h.server()
    h.bridge.open_session(SID, server["id"], 80, 24)
    result = h.bridge.update_server(server["id"], {**WEB, "host": "192.0.2.99", "nickname": "바뀐별명"})
    assert result["ok"] and result["server"]["host"] == "192.0.2.99"
    assert h.spawn.ptys[0].written == [] and h.spawn.ptys[0].alive


def test_deleting_a_server_closes_its_tabs_and_password(make):
    h = make()
    server = h.server()
    h.bridge.open_session(SID, server["id"], 80, 24)
    result = h.bridge.delete_server(server["id"])
    assert result == {"ok": True, "closed_sessions": [SID]}
    assert "exit\r" in h.spawn.ptys[0].written
    assert not h.secrets.has(server["id"])
    assert h.bridge.list_servers()["servers"] == []
    assert close_requested(h.bridge) is True


def test_probe_reports_each_server(make):
    h = make(online=False)
    server = h.server()
    assert h.bridge.probe() == {"ok": True, "status": {server["id"]: False}}


def test_probes_run_outside_the_calling_thread_and_its_event_loop(make):
    # probe_all은 자기 이벤트 루프를 돌린다. 루프가 이미 도는 스레드(또는 창 스레드)에서 부르면 안 된다
    h = make()
    server = h.server()
    seen = []

    def probe(hosts):
        try:
            asyncio.get_running_loop()
            running = True
        except RuntimeError:
            running = False
        seen.append((threading.current_thread(), running))
        return {x: True for x in hosts}

    h.probe = probe

    async def from_a_running_loop():
        return h.bridge.probe(), h.bridge.open_session(SID, server["id"], 80, 24)

    status, opened = asyncio.run(from_a_running_loop())
    assert status == {"ok": True, "status": {server["id"]: True}}
    assert opened == {"ok": True}
    assert len(seen) == 2
    assert all(thread is not threading.current_thread() and not running for thread, running in seen)


def test_a_failing_probe_is_logged_and_does_not_block_connecting(make, caplog):
    h = make()
    server = h.server()

    def broken(hosts):
        raise RuntimeError("probe 고장")

    h.probe = broken
    assert h.bridge.probe()["ok"] is False
    # 5985 확인을 못 했으면 TrustedHosts를 못 읽었을 때처럼 건너뛰고 접속 스크립트의 오류 분류에 맡긴다
    assert h.bridge.open_session(SID, server["id"], 80, 24) == {"ok": True}
    warnings = bridge_warnings(caplog)
    assert len(warnings) == 2 and all(w.exc_info for w in warnings)


# ---------- 엑셀 ----------


def test_excel_preview_hides_passwords_and_memos_and_applies_once(make, tmp_path):
    h = make()
    write_sheet(tmp_path / "s.xlsx", ["DB", None, "192.0.2.31", None, "opsadmin", "Excel-pw", "비공개-메모"])
    h.dialogs.open_path = str(tmp_path / "s.xlsx")
    preview = h.bridge.preview_excel()
    assert preview["ok"] and preview["counts"] == {"new": 1, "existing": 0, "error": 0}
    assert "Excel-pw" not in json.dumps(preview) and "비공개" not in json.dumps(preview, ensure_ascii=False)
    assert set(preview["rows"][0]) == {"row", "status", "name", "nickname", "host", "group", "username", "error"}
    applied = h.bridge.apply_excel(preview["preview_id"], False)
    assert applied == {"ok": True, "added": 1, "updated": 0, "skipped": 0, "errors": 0}
    assert h.bridge.apply_excel(preview["preview_id"], False)["ok"] is False  # 한 번만 쓸 수 있다
    h.dialogs.open_path = None
    assert h.bridge.preview_excel() == {"ok": False, "cancelled": True}


def test_an_unreadable_excel_file_is_reported(make, tmp_path):
    h = make()
    (tmp_path / "broken.xlsx").write_bytes(b"not a workbook")
    h.dialogs.open_path = str(tmp_path / "broken.xlsx")
    result = h.bridge.preview_excel()
    assert result["ok"] is False and "엑셀" in result["error"]


def test_a_failed_excel_apply_leaves_nothing_behind(make, tmp_path, monkeypatch):
    h = make()
    kept = h.server()  # 이미 있던 서버와 그 비밀번호는 그대로 둔다
    write_sheet(tmp_path / "s.xlsx", ["DB", None, "192.0.2.31", None, "opsadmin", "Excel-pw", None])
    h.dialogs.open_path = str(tmp_path / "s.xlsx")
    preview = h.bridge.preview_excel()

    def full_disk():
        raise OSError(28, "디스크 공간이 부족합니다")

    monkeypatch.setattr(h.inventory, "save", full_disk)
    result = h.bridge.apply_excel(preview["preview_id"], False)
    assert result["ok"] is False and "등록하지 못했습니다" in result["error"]
    monkeypatch.undo()
    assert h.bridge.list_servers() == {"ok": True, "servers": [kept]}  # 반쯤 등록한 서버가 메모리에 남지 않는다
    assert [p.name for p in (tmp_path / "secrets").iterdir()] == [f"{kept['id']}.dpapi"]  # 새 서버의 비밀번호 파일도


def test_a_failed_apply_that_cannot_reread_the_list_reports_the_broken_file(make, tmp_path, monkeypatch):
    h = make()
    write_sheet(tmp_path / "s.xlsx", ["DB", None, "192.0.2.31", None, "opsadmin", "Excel-pw", None])
    h.dialogs.open_path = str(tmp_path / "s.xlsx")
    preview = h.bridge.preview_excel()

    def break_the_file():
        (tmp_path / "inventory.json").write_text("{깨진 파일", encoding="utf-8")
        raise OSError(5, "액세스가 거부되었습니다")

    monkeypatch.setattr(h.inventory, "save", break_the_file)
    assert h.bridge.apply_excel(preview["preview_id"], False)["ok"] is False
    listed = h.bridge.list_servers()
    assert listed["ok"] is False and "inventory.json" in listed["error"]


def test_save_template_through_the_dialog(make, tmp_path):
    h = make()
    h.dialogs.save_path = str(tmp_path / "양식.xlsx")
    assert h.bridge.save_excel_template() == {"ok": True, "path": str(tmp_path / "양식.xlsx")}
    assert (tmp_path / "양식.xlsx").exists()
    assert h.dialogs.default_name.endswith(".xlsx")
    h.dialogs.save_path = None
    assert h.bridge.save_excel_template() == {"ok": False, "cancelled": True}


# ---------- 초기 설정 ----------


def test_trusted_hosts(make):
    h = make(trusted=[])
    h.server()
    assert h.bridge.missing_trusted_hosts() == {"ok": True, "readable": True, "missing": ["192.0.2.21"]}
    assert h.bridge.add_trusted_hosts() == {"ok": True}
    assert h.added == [["192.0.2.21"]]
    h.trusted = None
    assert h.bridge.missing_trusted_hosts() == {"ok": True, "readable": False, "missing": []}


def test_adding_trusted_hosts_reports_a_cancel_and_a_failure(make):
    h = make(trusted=[])
    h.server()
    h.add_result = AddResult.CANCELLED
    cancelled = h.bridge.add_trusted_hosts()
    assert cancelled["ok"] is False and cancelled["cancelled"] is True and cancelled["error"]
    h.add_result = AddResult.FAILED
    failed = h.bridge.add_trusted_hosts()
    assert failed["ok"] is False and "cancelled" not in failed
    assert "WinRM" in failed["error"] and "사용 안 함" in failed["error"]


def test_settings_and_setup_script(make):
    h = make()
    server = h.server()
    assert h.bridge.server_setup_script(server["id"], True)["ok"] is False  # IP 목록이 비어 있음
    assert h.bridge.save_settings(["198.51.100"])["ok"] is False
    assert h.bridge.save_settings(["198.51.100.50"]) == {"ok": True, "admin_ips": ["198.51.100.50"]}
    assert h.bridge.get_settings() == {"ok": True, "admin_ips": ["198.51.100.50"]}
    script = h.bridge.server_setup_script(server["id"], True)["script"]
    assert "@('198.51.100.50')" in script and "$Account = 'Administrator'" in script


# ---------- 세션 ----------


def test_open_session_prechecks(make):
    h = make(trusted=[])
    server = h.server()
    assert h.bridge.open_session(SID, server["id"], 80, 24)["need"] == "trustedhosts"
    h.trusted = None  # 읽을 수 없으면 건너뛴다
    h.online = False
    assert h.bridge.open_session(SID, server["id"], 80, 24)["need"] == "unreachable"
    h.online = True
    h.secrets.delete(server["id"])
    assert h.bridge.open_session(SID, server["id"], 80, 24)["need"] == "password"
    assert h.bridge.open_session("bad", server["id"], 80, 24)["ok"] is False
    assert h.bridge.open_session(SID, "a" * 32, 80, 24)["ok"] is False
    assert h.spawn.calls == []
    assert close_requested(h.bridge) is True  # 열지 못한 탭은 남지 않는다


def test_open_session_spawns_the_bootstrap_without_the_password(make):
    h = make()
    server = h.server("Secret-pw")
    assert h.bridge.open_session(SID, server["id"], 99999, 0) == {"ok": True}
    argv, rows, cols = h.spawn.calls[0]
    assert argv[0].lower().endswith("powershell.exe") and "-EncodedCommand" in argv
    assert not any("Secret-pw" in part for part in argv)
    assert (rows, cols) == (2, 1000)


def test_input_waits_for_the_ready_signal_and_each_send_is_its_own_write(make):
    h = make()
    server = h.server()
    h.bridge.open_session(SID, server["id"], 80, 24)
    pty = h.spawn.ptys[0]
    h.bridge.send_input(SID, "\x1b[?1;2c")  # 터미널의 응답은 신호 전에도 간다
    h.bridge.send_input(SID, "d")
    h.bridge.send_input(SID, "ir\r")
    assert pty.written == ["\x1b[?1;2c"]  # 안전장치가 걸렸다는 신호 전에는 친 글자를 쥐고 있다
    pty.feed(READY)
    assert wait_for(lambda: pty.written == ["\x1b[?1;2c", "d", "ir\r"])
    h.bridge.send_input(SID, "a")
    h.bridge.send_input(SID, "b")
    assert pty.written[-2:] == ["a", "b"]  # 묶지 않고 받은 그대로 하나씩 넘긴다


def test_no_ready_signal_ends_the_tab_as_not_ready(make):
    h = make(ready_timeout=0.1)
    server = h.server()
    h.bridge.open_session(SID, server["id"], 80, 24)
    h.bridge.send_input(SID, "dir\r")
    assert wait_for(lambda: ended(SID, "NOT_READY") in h.emitted)
    assert h.spawn.ptys[0].written == []


def test_output_is_pumped_to_the_screen(make):
    h = make()
    server = h.server()
    h.bridge.open_session(SID, server["id"], 80, 24)
    start_pump(h.bridge)
    try:
        h.spawn.ptys[0].feed("hello")
        assert wait_for(lambda: {SID: "hello"} in outputs(h.emitted))
    finally:
        shutdown(h.bridge)


def test_big_output_arrives_whole_in_capped_pieces(make):
    h = make()
    server = h.server()
    h.bridge.open_session(SID, server["id"], 80, 24)
    start_pump(h.bridge)
    try:
        h.spawn.ptys[0].feed("x" * 450_000)
        assert wait_for(lambda: sum(len(o.get(SID, "")) for o in outputs(h.emitted)) == 450_000, timeout=5)
        assert max(len(o[SID]) for o in outputs(h.emitted)) <= 200_000
    finally:
        shutdown(h.bridge)


def test_a_failing_screen_call_is_logged_as_a_warning_and_the_pump_goes_on(make, caplog):
    emitted = []

    def flaky(script):
        if not emitted:
            emitted.append(None)
            raise RuntimeError("창이 아직 없음")
        emitted.append(script)

    h = make(emit=flaky)
    server = h.server()
    h.bridge.open_session(SID, server["id"], 80, 24)
    start_pump(h.bridge)
    h.spawn.ptys[0].feed("one")
    assert wait_for(lambda: emitted)
    h.spawn.ptys[0].feed("two")
    assert wait_for(lambda: {SID: "two"} in outputs(emitted[1:]))
    [warning] = bridge_warnings(caplog)
    assert warning.exc_info is not None


def test_session_end_flushes_output_first(make):
    h = make()
    server = h.server()
    h.bridge.open_session(SID, server["id"], 80, 24)
    h.spawn.ptys[0].feed("bye")
    assert wait_for(lambda: h.bridge._batcher.pending() == 3)
    h.spawn.ptys[0].finish(20)
    assert wait_for(lambda: ended(SID, "DISCONNECTED") in h.emitted)
    output_index = next(i for i, s in enumerate(h.emitted) if "bye" in s)
    assert output_index < h.emitted.index(ended(SID, "DISCONNECTED"))


@pytest.mark.parametrize(
    ("trusted", "reason"),
    [(["*"], "AUTH_FAILED"), (["192.0.2.21"], "AUTH_FAILED"), ([], "NOT_TRUSTED"), (None, "NOT_TRUSTED")],
    ids=["wildcard", "listed", "missing", "unreadable"],
)
def test_not_trusted_for_a_host_already_in_trustedhosts_is_a_login_failure(make, trusted, reason):
    # 1312(0x8009030e) 오류는 TrustedHosts에 없을 때뿐 아니라 계정 정보가 틀렸을 때도 난다(docs/verification.md V3)
    h = make()
    server = h.server()
    h.bridge.open_session(SID, server["id"], 80, 24)
    h.trusted = trusted
    h.spawn.ptys[0].finish(12)
    assert wait_for(lambda: ended(SID, reason) in h.emitted)


def test_the_session_end_is_logged_with_the_server_and_the_host_the_tab_used(make, caplog):
    caplog.set_level(logging.INFO, logger="hq.bridge")
    h = make()
    server = h.server("Secret-pw")
    h.bridge.open_session(SID, server["id"], 80, 24)
    h.bridge.update_server(server["id"], {**WEB, "host": "192.0.2.99"})  # 열린 탭의 프로세스는 옛 host에 붙어 있다
    h.trusted = ["192.0.2.21"]
    h.spawn.ptys[0].finish(12)
    assert wait_for(lambda: ended(SID, "AUTH_FAILED") in h.emitted)  # 그 탭이 접속한 host로 판단한다
    [line] = [r.getMessage() for r in caplog.records if "세션 끝남" in r.getMessage()]
    assert server["id"] in line and "192.0.2.21" in line and "AUTH_FAILED" in line
    encoded = h.spawn.calls[0][0][-1]
    assert encoded not in caplog.text and "Secret-pw" not in caplog.text


def test_input_after_the_end_is_ignored_and_reconnect_works(make):
    h = make()
    server = h.server()
    h.bridge.open_session(SID, server["id"], 80, 24)
    h.spawn.ptys[0].finish(20)
    assert wait_for(lambda: any("onSessionEnded" in s for s in h.emitted))
    h.bridge.send_input(SID, "dir\r")
    assert h.spawn.ptys[0].written == []
    assert h.bridge.reconnect(SID, 80, 24) == {"ok": True}
    h.spawn.ptys[1].feed(READY)
    h.bridge.send_input(SID, "dir\r")
    assert wait_for(lambda: h.spawn.ptys[1].written == ["dir\r"])


def test_reconnect_needs_an_open_tab_and_a_listed_server(make):
    h = make()
    server = h.server()
    assert h.bridge.reconnect(SID, 80, 24)["ok"] is False
    h.bridge.open_session(SID, server["id"], 80, 24)
    assert h.bridge.reconnect(SID, 80, 24)["ok"] is False  # 아직 실행 중
    h.spawn.ptys[0].finish(20)
    assert wait_for(lambda: any("onSessionEnded" in s for s in h.emitted))
    h.secrets.delete(server["id"])
    assert h.bridge.reconnect(SID, 80, 24)["need"] == "password"
    assert len(h.spawn.calls) == 1


def test_a_powershell_that_cannot_start_is_reported_with_an_antivirus_hint(make, caplog):
    spawner = FakeSpawner()
    blocked = []

    def spawn(argv, rows, cols):
        if blocked:
            raise PermissionError(5, "액세스가 거부되었습니다")
        return spawner(argv, rows, cols)

    h = make(spawn=spawn)
    server = h.server()
    blocked.append(True)
    result = h.bridge.open_session(SID, server["id"], 80, 24)
    assert result["ok"] is False and "백신" in result["error"]
    assert close_requested(h.bridge) is True
    blocked.clear()
    assert h.bridge.open_session(SID, server["id"], 80, 24) == {"ok": True}
    spawner.ptys[0].finish(20)
    assert wait_for(lambda: any("onSessionEnded" in s for s in h.emitted))
    blocked.append(True)
    result = h.bridge.reconnect(SID, 80, 24)
    assert result["ok"] is False and "백신" in result["error"]
    warnings = bridge_warnings(caplog)
    assert len(warnings) == 2 and all(w.exc_info for w in warnings)


def test_a_second_open_with_the_same_id_is_refused(make):
    # 화면은 탭마다 새 id를 만든다. 같은 id가 두 번 오면 띄우지 않고 거절한다(같이 띄우면 하나가 고아가 된다)
    h = make()
    server = h.server()
    held = HeldProbe()
    h.probe = held
    thread, first = open_in_background(h, server["id"])
    assert held.entered.wait(2)
    second = h.bridge.open_session(SID, server["id"], 80, 24)
    assert second["ok"] is False and second["error"]
    assert held.calls == 1  # 사전 확인도 하지 않고 바로 거절했다
    held.release.set()
    thread.join(2)
    assert first == {"ok": True}
    assert h.bridge.open_session(SID, server["id"], 80, 24)["ok"] is False
    assert len(h.spawn.calls) == 1


@pytest.mark.parametrize("action", ["close_session", "delete_server", "shutdown"])
def test_a_tab_closed_during_its_prechecks_never_starts(make, action):
    h = make()
    server = h.server()
    held = HeldProbe()
    h.probe = held
    thread, result = open_in_background(h, server["id"])
    assert held.entered.wait(2)
    if action == "close_session":
        assert h.bridge.close_session(SID) == {"ok": True}
    elif action == "delete_server":
        assert h.bridge.delete_server(server["id"])["closed_sessions"] == [SID]
    else:
        shutdown(h.bridge)
    held.release.set()
    thread.join(2)
    assert result == {"ok": False, "cancelled": True}
    assert h.spawn.calls == []
    assert close_requested(h.bridge) is True


def test_a_tab_closed_while_its_powershell_starts_is_closed_right_after(make):
    spawner = FakeSpawner()
    entered, release = threading.Event(), threading.Event()

    def slow_spawn(argv, rows, cols):
        entered.set()
        release.wait(2)
        return spawner(argv, rows, cols)

    h = make(spawn=slow_spawn)
    server = h.server()
    thread, result = open_in_background(h, server["id"])
    assert entered.wait(2)
    h.bridge.close_session(SID)
    release.set()
    thread.join(2)
    assert result == {"ok": False, "cancelled": True}
    assert "exit\r" in spawner.ptys[0].written and spawner.ptys[0].closed
    assert close_requested(h.bridge) is True


def test_close_needs_confirmation_only_with_open_tabs(make):
    h = make()
    assert close_requested(h.bridge) is True
    server = h.server()
    h.bridge.open_session(SID, server["id"], 80, 24)
    assert close_requested(h.bridge) is False
    assert wait_for(lambda: "window.hq.onCloseRequested(1)" in h.emitted)
    h.bridge.quit()
    assert h.destroyed == [True]
    assert "exit\r" in h.spawn.ptys[0].written
    assert close_requested(h.bridge) is True
    assert h.bridge.open_session("f" * 32, server["id"], 80, 24)["ok"] is False  # 끝내는 중에는 새 탭을 열지 않는다


def test_a_second_shutdown_waits_for_the_one_still_closing_tabs(make):
    # quit이 탭을 닫는 사이 창이 닫히면 main도 shutdown을 부른다. 그것이 먼저 돌아오면 프로세스가 닫기 도중에 끝난다
    spawner = FakeSpawner(exit_on_exit=False)  # exit에 끝나지 않는 셸: 닫기가 close_timeout까지 걸린다
    h = make(spawn=spawner, close_timeout=0.6)
    server = h.server()
    assert h.bridge.open_session(SID, server["id"], 80, 24) == {"ok": True}
    quitting = threading.Thread(target=h.bridge.quit)
    quitting.start()
    assert wait_for(lambda: "\x03" in spawner.ptys[0].written)  # quit이 탭을 닫는 중
    shutdown(h.bridge)
    finished = spawner.ptys[0].closed
    quitting.join(5)
    assert finished and spawner.ptys[0].terminated


@pytest.mark.parametrize("close", [
    pytest.param(lambda bridge: bridge.close_session(SID), id="close_session"),  # 마지막 탭을 닫자마자 X
    pytest.param(close_all_sessions, id="close_all_sessions"),  # hq 화면이 다시 읽힌 직후 X
])
def test_shutdown_waits_for_tabs_that_are_still_closing(make, close):
    # 닫는 중인 탭은 목록에서 이미 빠져 창은 확인 없이 닫힌다. main의 shutdown이 먼저 돌아오면 Ctrl+C·exit를 보내던
    # 중에 프로세스가 끝나 서버 세션이 유휴 시간까지 남는다(AC-7). shutdown은 그 닫기가 끝날 때까지 기다린다
    spawner = FakeSpawner(exit_on_exit=False)  # exit에 끝나지 않는 셸: 닫기가 close_timeout까지 걸린다
    h = make(spawn=spawner, close_timeout=0.6)
    server = h.server()
    assert h.bridge.open_session(SID, server["id"], 80, 24) == {"ok": True}
    closing = threading.Thread(target=close, args=(h.bridge,))
    closing.start()
    assert wait_for(lambda: "\x03" in spawner.ptys[0].written)
    assert close_requested(h.bridge) is True  # 확인할 탭은 없다. 창은 닫히고 기다림은 shutdown이 맡는다
    shutdown(h.bridge)
    finished = spawner.ptys[0].closed
    closing.join(5)
    assert finished and spawner.ptys[0].terminated


class HeldSpawner(FakeSpawner):
    """프로세스를 띄우는 도중에 붙잡아 둔다(release를 기다린다)."""

    def __init__(self):
        super().__init__(exit_on_exit=False)
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, argv, rows, cols):
        self.entered.set()
        self.release.wait(5)
        return super().__call__(argv, rows, cols)


def test_shutdown_waits_for_a_tab_that_was_still_opening(make):
    # 띄우는 도중에 끝내면 open_session이 마무리하며 그 세션을 닫는다. shutdown은 그것까지 기다린다
    spawner = HeldSpawner()
    h = make(spawn=spawner, close_timeout=0.6)
    server = h.server()
    opening, _ = open_in_background(h, server["id"])
    assert spawner.entered.wait(2)
    seen = {}

    def quit_now():
        shutdown(h.bridge)
        seen["closed_when_shutdown_returned"] = bool(spawner.ptys) and spawner.ptys[0].closed

    stopping = threading.Thread(target=quit_now)
    stopping.start()
    time.sleep(0.2)  # shutdown이 기다리기 시작한 뒤에 프로세스가 뜬다
    spawner.release.set()
    stopping.join(5)
    opening.join(5)
    assert seen == {"closed_when_shutdown_returned": True}


def test_shutdown_stops_waiting_for_a_stuck_close_and_says_so(make, monkeypatch, caplog):
    # 닫기가 끝나지 않아도 HQ가 끝나지 못한 채 남지는 않는다
    monkeypatch.setattr(bridge_module, "SETTLE_TIMEOUT", 0.1)
    spawner = FakeSpawner(exit_on_exit=False)
    h = make(spawn=spawner, close_timeout=2.0)
    server = h.server()
    h.bridge.open_session(SID, server["id"], 80, 24)
    closing = threading.Thread(target=h.bridge.close_session, args=(SID,))
    closing.start()
    assert wait_for(lambda: "\x03" in spawner.ptys[0].written)
    started = time.monotonic()
    shutdown(h.bridge)
    assert time.monotonic() - started < 1.0
    assert not spawner.ptys[0].closed
    assert len(bridge_warnings(caplog)) == 1
    closing.join(5)


def test_close_all_sessions_closes_every_tab_but_keeps_taking_new_ones(make):
    h = make()
    server = h.server()
    h.bridge.open_session(SID, server["id"], 80, 24)
    h.bridge.open_session("d" * 32, server["id"], 80, 24)
    assert sorted(close_all_sessions(h.bridge)) == sorted([SID, "d" * 32])
    assert all(pty.closed for pty in h.spawn.ptys)
    assert close_requested(h.bridge) is True
    assert h.bridge.open_session("f" * 32, server["id"], 80, 24) == {"ok": True}


def test_passwords_never_reach_the_log(make, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    h = make()
    server = h.server("Secret-pw-1")
    h.bridge.set_password(server["id"], "Secret-pw-2")
    h.bridge.open_session(SID, server["id"], 80, 24)
    write_sheet(tmp_path / "s.xlsx", ["DB", None, "192.0.2.31", None, "opsadmin", "Secret-pw-3", None])
    h.dialogs.open_path = str(tmp_path / "s.xlsx")
    h.bridge.apply_excel(h.bridge.preview_excel()["preview_id"], True)
    h.spawn.ptys[0].finish(20)
    assert wait_for(lambda: any("onSessionEnded" in s for s in h.emitted))
    assert caplog.records
    assert not any(f"Secret-pw-{n}" in caplog.text for n in (1, 2, 3))
