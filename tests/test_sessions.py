import logging
import threading
import time

import pytest
from fakes import FakePty, FakeSpawner, wait_for
from winpty import WinptyError

from hq.sessions import ConPty, SessionError, SessionManager

SID = "a" * 32
SID2 = "b" * 32
SID3 = "c" * 32


def make(spawn):
    out, exits = [], []
    manager = SessionManager(
        spawn=spawn,
        on_output=lambda sid, data: out.append((sid, data)),
        on_exit=lambda sid, code: exits.append((sid, code)),
        poll_interval=0.01,
        drain_delay=0.02,
        close_timeout=0.3,
    )
    return manager, out, exits


def test_output_is_forwarded_in_order_and_size_is_passed():
    spawn = FakeSpawner()
    manager, out, _ = make(spawn)
    manager.open(SID, "s1", ["ps"], 100, 30)
    assert spawn.calls == [(["ps"], 30, 100)]
    spawn.ptys[0].feed("a")
    spawn.ptys[0].feed("b")
    assert wait_for(lambda: len(out) == 2)
    assert out == [(SID, "a"), (SID, "b")]


def test_exit_code_is_reported_once_and_the_tab_stays_open():
    spawn = FakeSpawner()
    manager, _, exits = make(spawn)
    manager.open(SID, "s1", ["ps"], 80, 24)
    spawn.ptys[0].finish(20)
    assert wait_for(lambda: exits)
    time.sleep(0.05)
    assert exits == [(SID, 20)]
    assert manager.running_count() == 0
    assert manager.open_count() == 1


def test_input_reaches_only_a_running_process():
    spawn = FakeSpawner()
    manager, _, exits = make(spawn)
    manager.open(SID, "s1", ["ps"], 80, 24)
    manager.write(SID, "x")
    spawn.ptys[0].finish(20)
    assert wait_for(lambda: exits)
    manager.write(SID, "y")
    assert spawn.ptys[0].written == ["x"]


def test_resize_goes_to_the_pty():
    spawn = FakeSpawner()
    manager, _, _ = make(spawn)
    manager.open(SID, "s1", ["ps"], 80, 24)
    manager.resize(SID, 120, 40)
    assert spawn.ptys[0].size == (40, 120)


def test_close_sends_ctrl_c_then_exit_and_reports_nothing():
    spawn = FakeSpawner()
    manager, out, exits = make(spawn)
    manager.open(SID, "s1", ["ps"], 80, 24)
    manager.close(SID)
    assert spawn.ptys[0].written == ["\x03", "exit\r"]
    time.sleep(0.1)
    assert exits == []
    assert manager.open_count() == 0
    spawn.ptys[0].feed("late")
    time.sleep(0.05)
    assert out == []


def test_close_kills_a_process_that_will_not_exit():
    spawn = FakeSpawner(exit_on_exit=False)
    manager, _, exits = make(spawn)
    manager.open(SID, "s1", ["ps"], 80, 24)
    manager.close(SID)
    assert spawn.ptys[0].terminated
    time.sleep(0.1)
    assert exits == []


def test_reconnect_replaces_the_process_after_it_ended():
    spawn = FakeSpawner()
    manager, out, exits = make(spawn)
    manager.open(SID, "s1", ["ps"], 80, 24)
    with pytest.raises(SessionError):
        manager.reconnect(SID, ["ps2"], 80, 24)  # 아직 실행 중
    spawn.ptys[0].finish(20)
    assert wait_for(lambda: exits)
    manager.reconnect(SID, ["ps2"], 90, 25)
    assert spawn.calls[-1] == (["ps2"], 25, 90)
    assert "다시 연결" in out[-1][1]
    spawn.ptys[1].feed("new")
    assert wait_for(lambda: out[-1] == (SID, "new"))
    assert manager.running_count() == 1


def test_rejects_bad_and_duplicate_ids():
    manager, _, _ = make(FakeSpawner())
    with pytest.raises(SessionError):
        manager.open("not-hex", "s1", ["ps"], 80, 24)
    manager.open(SID, "s1", ["ps"], 80, 24)
    with pytest.raises(SessionError):
        manager.open(SID, "s1", ["ps"], 80, 24)
    with pytest.raises(SessionError):
        manager.reconnect(SID3, ["ps"], 80, 24)


def test_lookup_by_server_and_session():
    manager, _, _ = make(FakeSpawner())
    manager.open(SID, "s1", ["ps"], 80, 24)
    manager.open(SID2, "s2", ["ps"], 80, 24)
    assert manager.sessions_for("s1") == [SID]
    assert manager.server_of(SID2) == "s2"
    assert manager.server_of(SID3) is None


def test_close_all_closes_stuck_sessions_in_parallel():
    spawn = FakeSpawner(exit_on_exit=False)
    manager, _, _ = make(spawn)
    for sid in (SID, SID2, SID3):
        manager.open(sid, "s", ["ps"], 80, 24)
    start = time.monotonic()
    manager.close_all()
    assert time.monotonic() - start < 0.9
    assert all(p.terminated for p in spawn.ptys)
    assert manager.open_count() == 0


# ---- PTY 놓기 (Ruling P1) ----


@pytest.mark.parametrize("exit_on_exit", [True, False], ids=["exits", "stuck"])
def test_close_releases_the_pty(exit_on_exit):
    spawn = FakeSpawner(exit_on_exit=exit_on_exit)
    manager, _, _ = make(spawn)
    manager.open(SID, "s1", ["ps"], 80, 24)
    manager.close(SID)
    assert spawn.ptys[0].closed


def test_close_releases_the_pty_of_an_ended_session():
    spawn = FakeSpawner()
    manager, _, exits = make(spawn)
    manager.open(SID, "s1", ["ps"], 80, 24)
    spawn.ptys[0].finish(20)
    assert wait_for(lambda: exits)
    manager.close(SID)
    assert spawn.ptys[0].closed
    assert spawn.ptys[0].written == []  # 이미 끝난 프로세스에는 아무것도 보내지 않는다


def test_reconnect_releases_the_old_pty_before_spawning_the_new_one():
    spawn = FakeSpawner()
    old_closed_at_spawn: list[bool] = []

    def spawner(argv, rows, cols):
        if spawn.ptys:
            old_closed_at_spawn.append(spawn.ptys[0].closed)
        return spawn(argv, rows, cols)

    manager, _, exits = make(spawner)
    manager.open(SID, "s1", ["ps"], 80, 24)
    spawn.ptys[0].finish(20)
    assert wait_for(lambda: exits)
    manager.reconnect(SID, ["ps2"], 80, 24)
    assert old_closed_at_spawn == [True]
    assert not spawn.ptys[1].closed


# ---- 동시에 들어온 호출 ----


class GatedSpawner(FakeSpawner):
    """gate가 열릴 때까지 두 번째 spawn부터 멈춘다(다시 연결 도중을 흉내 낸다)."""

    def __init__(self) -> None:
        super().__init__()
        self.waiting = threading.Event()
        self.gate = threading.Event()

    def __call__(self, argv, rows, cols):
        if self.ptys:
            self.waiting.set()
            self.gate.wait(2)
        return super().__call__(argv, rows, cols)


def _ended_session(spawn):
    manager, out, exits = make(spawn)
    manager.open(SID, "s1", ["ps"], 80, 24)
    spawn.ptys[0].finish(20)
    assert wait_for(lambda: exits)
    return manager, out, exits


def _reconnect_in_background(manager) -> tuple[threading.Thread, list[BaseException]]:
    errors: list[BaseException] = []

    def run():
        try:
            manager.reconnect(SID, ["ps2"], 80, 24)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, errors


def test_a_second_reconnect_while_the_first_is_starting_is_rejected():
    spawn = GatedSpawner()
    manager, _, _ = _ended_session(spawn)
    thread, errors = _reconnect_in_background(manager)
    assert spawn.waiting.wait(2)
    with pytest.raises(SessionError):
        manager.reconnect(SID, ["ps3"], 80, 24)
    spawn.gate.set()
    thread.join(2)
    assert errors == []
    assert len(spawn.ptys) == 2  # 프로세스는 하나만 새로 떴다
    assert manager.running_count() == 1


def test_closing_during_a_reconnect_ends_the_new_process_too():
    spawn = GatedSpawner()
    manager, _, exits = _ended_session(spawn)
    thread, _ = _reconnect_in_background(manager)
    assert spawn.waiting.wait(2)
    closer = threading.Thread(target=manager.close, args=(SID,))
    closer.start()
    time.sleep(0.05)
    spawn.gate.set()
    thread.join(2)
    closer.join(2)
    assert manager.open_count() == 0
    assert len(spawn.ptys) == 2
    assert not spawn.ptys[1].alive
    assert spawn.ptys[1].closed
    time.sleep(0.1)
    assert exits == [(SID, 20)]  # 닫으면서 끝낸 새 프로세스는 보고하지 않는다


def test_input_typed_during_a_reconnect_reaches_the_new_process():
    spawn = GatedSpawner()
    manager, _, _ = _ended_session(spawn)
    thread, _ = _reconnect_in_background(manager)
    assert spawn.waiting.wait(2)
    writer = threading.Thread(target=manager.write, args=(SID, "x"))
    writer.start()
    time.sleep(0.05)
    spawn.gate.set()
    thread.join(2)
    writer.join(2)
    assert spawn.ptys[0].written == []
    assert spawn.ptys[1].written == ["x"]


# ---- 예상하지 못한 오류는 warning으로 남긴다 (Ruling P7) ----


def _warnings(caplog):
    return [r for r in caplog.records if r.name == "hq.sessions" and r.levelno == logging.WARNING and r.exc_info]


def test_a_read_failure_is_logged_as_a_warning(caplog):
    spawn = FakeSpawner()
    manager, _, _ = make(spawn)
    manager.open(SID, "s1", ["ps"], 80, 24)
    spawn.ptys[0].fail(RuntimeError("boom"))
    assert wait_for(lambda: _warnings(caplog))


def test_a_failing_output_callback_is_logged_and_reading_goes_on(caplog):
    spawn = FakeSpawner()
    seen = []

    def on_output(sid, data):
        if data == "bad":
            raise RuntimeError("boom")
        seen.append(data)

    manager = SessionManager(spawn=spawn, on_output=on_output, on_exit=lambda s, c: None, poll_interval=0.01)
    manager.open(SID, "s1", ["ps"], 80, 24)
    spawn.ptys[0].feed("bad")
    spawn.ptys[0].feed("good")
    assert wait_for(lambda: seen == ["good"])
    assert _warnings(caplog)


def test_a_failing_exit_callback_is_logged(caplog):
    spawn = FakeSpawner()

    def on_exit(sid, code):
        raise RuntimeError("boom")

    manager = SessionManager(
        spawn=spawn, on_output=lambda s, d: None, on_exit=on_exit, poll_interval=0.01, drain_delay=0.01
    )
    manager.open(SID, "s1", ["ps"], 80, 24)
    spawn.ptys[0].finish(20)
    assert wait_for(lambda: _warnings(caplog))


class BrokenWritePty(FakePty):
    def write(self, text: str) -> int:
        raise OSError("pipe broken")


def test_close_logs_a_failed_write_and_still_kills_and_releases(caplog):
    ptys: list[BrokenWritePty] = []

    def spawner(argv, rows, cols):
        ptys.append(BrokenWritePty())
        return ptys[-1]

    manager, _, _ = make(spawner)
    manager.open(SID, "s1", ["ps"], 80, 24)
    manager.close(SID)
    assert ptys[0].terminated
    assert ptys[0].closed
    assert _warnings(caplog)


# ---- 첫 exit이 버려져도 강제 종료 전에 한 번 더 친다 (Ruling R12-1) ----


class SwallowsFirstExitPty(FakePty):
    """Ctrl+C 바로 뒤에 온 입력이 버려지듯 첫 'exit\\r'을 흘려 버린다."""

    def write(self, text: str) -> int:
        if text == "exit\r" and "exit\r" not in self.written:
            self.written.append(text)
            return len(text)
        return super().write(text)


def test_close_types_exit_again_at_half_the_timeout_when_the_first_was_swallowed():
    ptys: list[SwallowsFirstExitPty] = []

    def spawner(argv, rows, cols):
        ptys.append(SwallowsFirstExitPty())
        return ptys[-1]

    manager, _, exits = make(spawner)  # close_timeout=0.3
    manager.open(SID, "s1", ["ps"], 80, 24)
    start = time.monotonic()
    manager.close(SID)
    assert time.monotonic() - start >= 0.2  # Ctrl+C 뒤 0.1초 + close_timeout의 절반을 기다린 뒤에 다시 쳤다
    assert ptys[0].written == ["\x03", "exit\r", "exit\r"]
    assert not ptys[0].terminated
    assert ptys[0].exitstatus == 0
    assert ptys[0].closed
    time.sleep(0.1)
    assert exits == []


# ---- ConPty: 살아 있는 동안의 읽기 오류는 늦은 EOF가 아니다 (Ruling R12-1) ----


class StubWinpty:
    """winpty.PTY 대신 ConPty에 넣는다. read()는 늘 WinptyError를 던진다."""

    pid = 0

    def __init__(self, *, alive: bool) -> None:
        self.alive = alive

    def read(self, blocking: bool = False) -> str:
        raise WinptyError("read failed")

    def isalive(self) -> bool:
        return self.alive

    def get_exitstatus(self) -> int | None:
        return None if self.alive else 0

    def cancel_io(self) -> bool:
        return True


def test_a_conpty_read_error_while_the_shell_runs_is_logged_as_a_warning(caplog):
    stub = StubWinpty(alive=True)
    manager, _, exits = make(lambda argv, rows, cols: ConPty(stub))
    manager.open(SID, "s1", ["ps"], 80, 24)
    assert wait_for(lambda: _warnings(caplog))
    stub.alive = False  # 감시 스레드를 끝낸다(close는 부르지 않는다. 가짜 pid를 강제 종료하지 않게)
    assert wait_for(lambda: exits)


def test_conpty_read_reports_eof_once_the_shell_has_ended_or_the_pty_is_closed():
    with pytest.raises(EOFError):
        ConPty(StubWinpty(alive=False)).read()  # ConPTY의 늦은 EOF
    closed = ConPty(StubWinpty(alive=True))
    closed.close()
    with pytest.raises(EOFError):
        closed.read()
