import logging
import threading
import time

import pytest
from fakes import FakePty, FakeSpawner, wait_for
from winpty import WinptyError

from hq import sessions
from hq.bootstrap import NOT_READY_CODE
from hq.sessions import ConPty, SessionError, SessionManager

SID = "a" * 32
SID2 = "b" * 32
SID3 = "c" * 32
MARKER = "hq:test-ready"


def make(spawn, **options):
    out, exits = [], []
    timings = {"poll_interval": 0.01, "drain_delay": 0.02, "close_timeout": 0.3}
    manager = SessionManager(
        spawn=spawn,
        on_output=lambda sid, data: out.append((sid, data)),
        on_exit=lambda sid, code: exits.append((sid, code)),
        **{**timings, **options},
    )
    return manager, out, exits


def gated(spawn, **options):
    """준비 신호(MARKER)를 기다리는 관리자."""
    return make(spawn, ready_marker=MARKER, **options)


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


# ---- 가짜 PTY도 닫으면 어댑터처럼 행동한다 ----


@pytest.mark.parametrize("make_pty", [FakePty, lambda: ConPty(StubWinpty(alive=True))], ids=["fake", "adapter"])
def test_closing_a_live_pty_ends_it_and_refuses_input(make_pty):
    pty = make_pty()
    pty.close()
    assert not pty.isalive()  # 의사 콘솔을 닫으면 그 안의 프로세스도 끝난다
    with pytest.raises(EOFError):
        pty.write("x")
    with pytest.raises(EOFError):
        pty.read()
    pty.close()  # 여러 번 불러도 된다


# ---- 준비 신호: 안전장치가 걸렸다는 신호가 오기 전에는 입력을 쥐고 있는다 (spec 7.3, Task 12b) ----

TITLE = f"\x1b]0;{MARKER}\x1b\\"  # ConPTY가 콘솔 제목 바꾸기를 내보내는 모양


def _warning_messages(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "hq.sessions" and r.levelno == logging.WARNING]


def test_input_typed_before_the_ready_signal_is_held_then_sent_in_typed_order():
    spawn = FakeSpawner()
    manager, out, _ = gated(spawn)
    manager.open(SID, "s1", ["ps"], 80, 24)
    manager.write(SID, "dir")
    manager.write(SID, "\r")
    spawn.ptys[0].feed("Windows PowerShell")
    assert wait_for(lambda: len(out) == 1)
    assert spawn.ptys[0].written == []
    spawn.ptys[0].feed(TITLE)
    assert wait_for(lambda: spawn.ptys[0].written == ["dir", "\r"])
    manager.write(SID, "x")  # 신호 뒤에는 바로 넘긴다
    assert spawn.ptys[0].written == ["dir", "\r", "x"]
    assert wait_for(lambda: len(out) == 2)
    assert out == [(SID, "Windows PowerShell"), (SID, TITLE)]  # 출력은 신호까지 그대로 넘긴다


def test_a_ready_signal_split_across_reads_still_counts():
    spawn = FakeSpawner()
    manager, out, _ = gated(spawn)
    manager.open(SID, "s1", ["ps"], 80, 24)
    manager.write(SID, "x")
    for ch in MARKER[:-1]:  # 처음부터 한 글자씩 나눠 읽혀도 이어서 본다
        spawn.ptys[0].feed(ch)
    assert wait_for(lambda: len(out) == len(MARKER) - 1)
    assert spawn.ptys[0].written == []
    spawn.ptys[0].feed(MARKER[-1])
    assert wait_for(lambda: spawn.ptys[0].written == ["x"])


def test_text_that_only_resembles_the_ready_signal_opens_nothing():
    spawn = FakeSpawner()
    manager, out, _ = gated(spawn)
    manager.open(SID, "s1", ["ps"], 80, 24)
    manager.write(SID, "x")
    spawn.ptys[0].feed(MARKER[:-1] + "!")
    spawn.ptys[0].feed("!" + MARKER[1:])
    assert wait_for(lambda: len(out) == 2)
    assert spawn.ptys[0].written == []


class BlockingWritePty(FakePty):
    """block_on을 쓰려 하면 release가 열릴 때까지 멈춘다."""

    def __init__(self, block_on: str) -> None:
        super().__init__()
        self._block_on = block_on
        self.entered = threading.Event()
        self.release = threading.Event()

    def write(self, text: str) -> int:
        if text == self._block_on:
            self.entered.set()
            self.release.wait(2)
        return super().write(text)


def test_a_write_racing_the_flush_waits_its_turn():
    pty = BlockingWritePty(block_on="1")
    manager, _, _ = gated(lambda argv, rows, cols: pty)
    manager.open(SID, "s1", ["ps"], 80, 24)
    for key in ("1", "2", "3"):
        manager.write(SID, key)
    pty.feed(MARKER)
    assert pty.entered.wait(2)  # 읽기 스레드가 쥐고 있던 입력을 보내는 중이다
    late = threading.Thread(target=manager.write, args=(SID, "4"))
    late.start()
    time.sleep(0.05)
    pty.release.set()
    late.join(2)
    assert wait_for(lambda: pty.written == ["1", "2", "3", "4"])


def test_no_ready_signal_in_time_ends_the_process_as_not_ready(caplog):
    spawn = FakeSpawner()
    manager, _, exits = gated(spawn, ready_timeout=0.2)
    start = time.monotonic()
    manager.open(SID, "s1", ["ps"], 80, 24)
    manager.write(SID, "Remove-Item C:\\data -Recurse\r")
    assert wait_for(lambda: exits)
    assert time.monotonic() - start >= 0.2
    time.sleep(0.1)
    assert exits == [(SID, NOT_READY_CODE)]  # 강제로 끝낸 프로세스의 종료 코드(1)는 따로 보고하지 않는다
    assert spawn.ptys[0].terminated
    assert spawn.ptys[0].written == []
    assert any(SID[:8] in message for message in _warning_messages(caplog))


class StubbornPty(FakePty):
    """terminate()를 받고도 한동안 살아 있는 프로세스."""

    def terminate(self, force: bool = False) -> bool:
        self.terminated = True
        return False


def test_a_ready_signal_after_the_timeout_releases_nothing():
    pty = StubbornPty()
    manager, out, exits = gated(lambda argv, rows, cols: pty, ready_timeout=0.1)
    manager.open(SID, "s1", ["ps"], 80, 24)
    manager.write(SID, "x")
    assert wait_for(lambda: pty.terminated)
    manager.write(SID, "y")
    pty.feed(MARKER)
    assert wait_for(lambda: out)
    manager.write(SID, "z")  # 늦게 온 신호가 문을 다시 열지 않았다
    pty.finish(5)
    assert wait_for(lambda: exits)
    time.sleep(0.05)
    assert pty.written == []
    assert exits == [(SID, NOT_READY_CODE)]


def test_a_process_that_ends_before_the_ready_signal_reports_its_own_code():
    spawn = FakeSpawner()
    manager, _, exits = gated(spawn, ready_timeout=0.2)
    manager.open(SID, "s1", ["ps"], 80, 24)
    manager.write(SID, "x")
    spawn.ptys[0].finish(14)
    assert wait_for(lambda: exits)
    time.sleep(0.3)  # ready_timeout이 지나도 NOT_READY를 더 보고하지 않는다
    assert exits == [(SID, 14)]
    assert not spawn.ptys[0].terminated
    assert spawn.ptys[0].written == []


def test_closing_a_tab_that_is_not_ready_reports_nothing_and_never_sends_held_input():
    spawn = FakeSpawner(exit_on_exit=False)
    manager, _, exits = gated(spawn, ready_timeout=0.2, close_timeout=0.6)  # 닫는 동안 ready_timeout이 지난다
    manager.open(SID, "s1", ["ps"], 80, 24)
    manager.write(SID, "x")
    manager.close(SID)
    assert spawn.ptys[0].written == ["\x03", "exit\r", "exit\r"]  # 닫기 입력만 갔다
    assert spawn.ptys[0].terminated
    time.sleep(0.1)
    assert exits == []


def test_reconnect_holds_input_until_the_new_process_signals_ready():
    spawn = FakeSpawner()
    manager, _, exits = gated(spawn)
    manager.open(SID, "s1", ["ps"], 80, 24)
    manager.write(SID, "a")
    spawn.ptys[0].feed(MARKER)
    assert wait_for(lambda: spawn.ptys[0].written == ["a"])
    spawn.ptys[0].finish(20)
    assert wait_for(lambda: exits)
    manager.reconnect(SID, ["ps2"], 80, 24)
    manager.write(SID, "b")
    time.sleep(0.05)
    assert spawn.ptys[1].written == []
    spawn.ptys[1].feed(MARKER)
    assert wait_for(lambda: spawn.ptys[1].written == ["b"])
    assert spawn.ptys[0].written == ["a"]


@pytest.mark.parametrize("marker", [None, MARKER], ids=["no-gate", "gate"])
def test_input_and_resize_sent_while_reconnect_swaps_the_process_reach_the_new_one(marker):
    # 다시 연결은 구분선을 내보낸 뒤 새 프로세스를 띄운다. 그 사이에 들어온 입력과 크기 바꾸기가 끝난 프로세스로
    # 가서 사라지면 안 된다(구분선을 받은 on_output이 바로 치는 것으로 그 틈을 흉내 낸다).
    spawn = FakeSpawner()
    exits = []

    def on_output(sid, data):
        if "다시 연결" in data:
            manager.write(sid, "x")
            manager.resize(sid, 132, 50)

    manager = SessionManager(
        spawn=spawn,
        on_output=on_output,
        on_exit=lambda sid, code: exits.append(code),
        poll_interval=0.01,
        drain_delay=0.02,
        ready_marker=marker,
    )
    manager.open(SID, "s1", ["ps"], 80, 24)
    spawn.ptys[0].finish(20)
    assert wait_for(lambda: exits)
    manager.reconnect(SID, ["ps2"], 80, 24)
    assert spawn.calls[-1] == (["ps2"], 50, 132)
    if marker is not None:
        spawn.ptys[1].feed(marker)
    assert wait_for(lambda: spawn.ptys[1].written == ["x"])
    assert spawn.ptys[0].written == []


def test_without_a_ready_marker_input_goes_straight_through_and_nothing_times_out():
    spawn = FakeSpawner()
    manager, _, exits = make(spawn, ready_timeout=0.05)
    manager.open(SID, "s1", ["ps"], 80, 24)
    manager.write(SID, "x")
    assert spawn.ptys[0].written == ["x"]
    time.sleep(0.15)
    assert exits == []
    assert not spawn.ptys[0].terminated


# xterm.js가 ConPTY의 질의에 답하는 응답. ConPTY는 시작할 때 DA1 답을 기다리므로 쥐고 있으면 신호가 늦거나 안 온다.
REPLIES = [
    "\x1b[?1;0c",  # DA1
    "\x1b[>0;276;0c",  # DA2
    "\x1b[24;80R",  # 커서 위치
    "\x1b[0n",  # 장치 상태
    "\x1b[I",  # 포커스 들어옴
    "\x1b[O",  # 포커스 나감
    "\x1b[I\x1b[?1;0c",  # 응답 여러 개가 한 번에
]


def test_terminal_replies_pass_the_gate_before_ready():
    spawn = FakeSpawner()
    manager, _, _ = gated(spawn)
    manager.open(SID, "s1", ["ps"], 80, 24)
    for reply in REPLIES:
        manager.write(SID, reply)
    assert spawn.ptys[0].written == REPLIES


@pytest.mark.parametrize(
    "chunk",
    ["\x1b[?1;0cdir\r", "\x1b[24;80R\r", "\x1b[A", "\x1b[0n\x1b", "\r", "\x1b[?1;0c\n"],
    ids=["reply+command", "reply+enter", "arrow-key", "reply+esc", "enter", "reply+newline"],
)
def test_a_chunk_with_anything_besides_terminal_replies_is_held(chunk):
    spawn = FakeSpawner()
    manager, _, _ = gated(spawn)
    manager.open(SID, "s1", ["ps"], 80, 24)
    manager.write(SID, chunk)
    assert spawn.ptys[0].written == []
    spawn.ptys[0].feed(MARKER)
    assert wait_for(lambda: spawn.ptys[0].written == [chunk])


def test_held_input_keeps_only_whole_writes_up_to_the_first_that_does_not_fit(caplog):
    # 붙여넣은 줄을 반만 남기면 그 반쪽이 원격 프롬프트에 남아 다음 Enter에 실행될 수 있다. 그래서 들어가지 않는
    # 입력은 통째로 버리고, 그 뒤의 입력도 모두 버린다(쥐고 있는 것은 늘 친 순서의 앞부분, 온전한 입력들뿐이다).
    limit = sessions.HELD_INPUT_LIMIT
    spawn = FakeSpawner()
    manager, _, _ = gated(spawn)
    manager.open(SID, "s1", ["ps"], 80, 24)
    manager.write(SID, "a" * (limit - 4))
    manager.write(SID, "bc")  # 들어간다(2자 남음)
    manager.write(SID, "def\r")  # 들어가지 않는다: 통째로 버린다
    manager.write(SID, "g")  # 자리는 있지만 앞 입력을 버렸으므로 버린다
    spawn.ptys[0].feed(MARKER)
    assert wait_for(lambda: spawn.ptys[0].written == ["a" * (limit - 4), "bc"])
    time.sleep(0.05)
    assert spawn.ptys[0].written == ["a" * (limit - 4), "bc"]
    messages = _warning_messages(caplog)
    assert len(messages) == 1, messages
    assert SID[:8] in messages[0]


def test_a_write_that_exactly_fills_the_held_input_is_kept():
    limit = sessions.HELD_INPUT_LIMIT
    spawn = FakeSpawner()
    manager, _, _ = gated(spawn)
    manager.open(SID, "s1", ["ps"], 80, 24)
    manager.write(SID, "a" * (limit - 2))
    manager.write(SID, "bc")
    spawn.ptys[0].feed(MARKER)
    assert wait_for(lambda: spawn.ptys[0].written == ["a" * (limit - 2), "bc"])


def test_a_tab_stuck_in_a_write_does_not_stall_other_tabs_when_its_process_ends():
    # 한 탭의 pty.write가 막혀 그 탭의 잠금을 쥐고 있는 동안 그 프로세스가 끝나도, 감시 스레드가 관리자 잠금을
    # 쥔 채 그 탭의 잠금을 기다리지 않는다. 기다리면 모든 탭의 관리자 호출이 함께 멈춘다.
    stuck = BlockingWritePty(block_on="1")
    other = FakePty()
    ptys = iter([stuck, other])
    manager, _, exits = make(lambda argv, rows, cols: next(ptys))
    manager.open(SID, "s1", ["ps"], 80, 24)
    manager.open(SID2, "s2", ["ps"], 80, 24)
    writer = threading.Thread(target=manager.write, args=(SID, "1"))
    writer.start()
    assert stuck.entered.wait(2)
    stuck.finish(0)
    time.sleep(0.2)  # 감시 스레드가 끝 처리에 들어간다
    start = time.monotonic()
    manager.write(SID2, "x")
    assert time.monotonic() - start < 0.5
    assert other.written == ["x"]
    stuck.release.set()
    writer.join(2)
    assert wait_for(lambda: exits == [(SID, 0)])
