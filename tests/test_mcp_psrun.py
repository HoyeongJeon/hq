"""hq.mcp.psrun을 가짜 프로세스로 확인한다: 표준 오류의 CLIXML 지우기, taskkill이 실패하거나 멈출 때, 띄운 뒤의 예외.

실제 프로세스를 띄우지 않는다. 시험마다 subprocess.Popen과 subprocess.run을 바꿔, 가짜 pid로 taskkill이 돌지 않게 한다.
"""

import io
import itertools
import os
import subprocess
import threading
import time
import tracemalloc
from pathlib import Path

import pytest

from hq.mcp import psrun
from hq.mcp.psrun import run_ps_template

TEMPLATE = Path("t.ps1")  # 가짜 프로세스는 읽지 않는다
# 이 PC의 PowerShell 5.1이 실제로 쓴 모양(2026-11-11): 첫 기록 때 표시 줄, 끝날 때 <Objs> 덩어리(뒤에 줄바꿈 없음)
MARKER = "#< CLIXML\r\n"
BLOCK = (
    '<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'
    '<S S="Error">x_x000D__x000A_</S><S S="warning">y</S></Objs>'
)
RAW = ("앞\r\n" + MARKER + "뒤\r\n" + BLOCK + "끝\r\n").encode("utf-8")
CLEAN = "앞\n뒤\n끝\n"


class Pipe:
    """read1이 정해진 바이트 조각을 차례로 돌려주는 가짜 파이프."""

    def __init__(self, chunks) -> None:
        self._chunks = iter(chunks)

    def read1(self, size: int = -1) -> bytes:
        return next(self._chunks, b"")

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class FakeProc:
    """ends=False면 스스로 끝나지 않는다. dies=False면 kill()을 받아도 끝나지 않는다."""

    pid = 0

    def __init__(self, *, err=(), out=(), ends: bool = True, dies: bool = True) -> None:
        self.stdin = io.BytesIO()
        self.stdout = Pipe(out)
        self.stderr = Pipe(err)
        self.ends = ends
        self.dies = dies
        self.killed = False
        self.waits: list[float | None] = []

    def wait(self, timeout: float | None = None) -> int:
        self.waits.append(timeout)
        if self.ends or (self.killed and self.dies):
            return 0
        raise subprocess.TimeoutExpired("powershell.exe", timeout)

    def kill(self) -> None:
        self.killed = True


class SlowProc(FakeProc):
    """스스로 끝나지 않고, 끝내기 전의 wait(timeout)은 그 시간만큼 실제로 기다린다."""

    def wait(self, timeout: float | None = None) -> int:
        if not self.killed:
            time.sleep(timeout)
        return super().wait(timeout)


@pytest.fixture
def runs(monkeypatch):
    """taskkill을 띄우는 대신 그 인자를 모은다."""
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(psrun.subprocess, "run", fake_run)
    return calls


def use(monkeypatch, proc: FakeProc) -> FakeProc:
    monkeypatch.setattr(psrun.subprocess, "Popen", lambda argv, **kwargs: proc)
    return proc


def unreadable(monkeypatch, path: Path) -> None:
    """Windows가 path의 속성 읽기를 거부하듯 os.stat이 PermissionError를 던지게 한다. Path.exists는 다시 던진다.

    Python 3.12의 os.path.exists(nt._path_exists)는 바꾼 os.stat을 부르지 않는다. 그래서 이 도우미를 쓰는 시험은
    확인이 더는 os.stat을 거치지 않음을 보일 뿐, 확인 안에서 난 오류를 삼키는 것까지 보이지는 않는다.
    """
    real_stat = os.stat

    def stat(target, *args, **kwargs):
        if isinstance(target, (str, os.PathLike)) and os.fspath(target) == os.fspath(path):
            raise PermissionError(13, "액세스가 거부되었습니다", os.fspath(path))
        return real_stat(target, *args, **kwargs)

    monkeypatch.setattr(os, "stat", stat)


@pytest.mark.usefixtures("runs")
def test_clixml_is_removed_and_plain_lines_around_it_are_kept_wherever_the_chunks_split(monkeypatch):
    # 표시 줄, <Objs, </Objs>, 한글, \r\n의 안쪽을 포함해 모든 자리에서 갈라 본다
    splits = [[RAW]] + [[RAW[:k], RAW[k:]] for k in range(1, len(RAW))] + [[bytes([b]) for b in RAW]]
    for chunks in splits:
        use(monkeypatch, FakeProc(err=chunks))
        result = run_ps_template(TEMPLATE, {}, timeout=5)
        assert (result.stderr, result.total_chars) == (CLEAN, len(CLEAN)), chunks  # 지운 글자는 세지 않는다


@pytest.mark.usefixtures("runs")
def test_no_piece_of_clixml_is_kept_wherever_the_limit_falls(monkeypatch):
    for keep_chars in range(len(CLEAN) + 1):
        use(monkeypatch, FakeProc(err=[RAW]))
        result = run_ps_template(TEMPLATE, {}, timeout=5, keep_chars=keep_chars)
        assert (result.stderr, result.total_chars) == (CLEAN[:keep_chars], len(CLEAN))


@pytest.mark.usefixtures("runs")
def test_only_standard_error_is_cleaned_and_only_after_the_marker(monkeypatch):
    # 표시 줄이 오기 전의 <Objs는 PowerShell이 쓴 것이 아니다. 표준 출력은 손대지 않는다
    plain = "<Objs> 그대로\n"
    use(monkeypatch, FakeProc(out=[RAW], err=[plain.encode("utf-8")]))
    result = run_ps_template(TEMPLATE, {}, timeout=5)
    assert result.stdout == RAW.decode("utf-8").replace("\r\n", "\n")
    assert result.stderr == plain


@pytest.mark.usefixtures("runs")
def test_a_block_far_larger_than_the_limit_is_dropped_without_being_held_in_memory(monkeypatch):
    piece = b'<S S="Error">' + b"x" * 65_000 + b"</S>"
    chunks = itertools.chain([b'#< CLIXML\r\n<Objs Version="1.1.0.1">'], itertools.repeat(piece, 320), [b"</Objs>"])
    use(monkeypatch, FakeProc(err=chunks))  # 약 20MB
    tracemalloc.start()
    try:
        result = run_ps_template(TEMPLATE, {}, timeout=5, keep_chars=100)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert (result.stderr, result.total_chars) == ("", 0)
    assert peak < 2_000_000


@pytest.mark.parametrize(
    "failure",
    [OSError("taskkill을 띄우지 못함"), subprocess.TimeoutExpired("taskkill.exe", 10)],
    ids=["fails", "hangs"],
)
def test_the_direct_child_is_still_ended_when_taskkill_fails_or_hangs(monkeypatch, failure):
    timeouts = []

    def broken_run(argv, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        raise failure

    monkeypatch.setattr(psrun.subprocess, "run", broken_run)
    proc = use(monkeypatch, FakeProc(ends=False))
    result = run_ps_template(TEMPLATE, {}, timeout=0.01)
    assert result.killed and result.code is None
    assert timeouts == [10] and proc.killed and proc.waits == [0.01, 10]


@pytest.mark.usefixtures("runs")
def test_the_stop_grace_never_extends_the_timeout(monkeypatch, tmp_path):
    # 멈춤 파일이 시간 한도 1초 전에 생겨도 stop_grace(10초)를 다 주지 않고 원래 한도에서 끝낸다
    stop = tmp_path / "stop"
    proc = use(monkeypatch, SlowProc(ends=False))
    timer = threading.Timer(0.5, stop.touch)
    timer.start()
    began = time.monotonic()
    try:
        result = run_ps_template(TEMPLATE, {}, timeout=1.5, stop_path=stop, stop_grace=10)
    finally:
        timer.cancel()
    assert result.killed and proc.killed and 1.4 < time.monotonic() - began < 3


@pytest.mark.usefixtures("runs")
def test_a_stop_path_that_cannot_be_checked_is_treated_as_absent(monkeypatch, tmp_path):
    # 멈춤 파일을 보다 난 오류는 "파일 없음"이다. 프로세스는 평소처럼 기다리고, 끝나면 그 코드와 출력을 돌려준다
    stop = tmp_path / "stop"
    unreadable(monkeypatch, stop)
    proc = use(monkeypatch, FakeProc(out=["끝\n".encode()]))
    result = run_ps_template(TEMPLATE, {}, timeout=5, stop_path=stop, stop_grace=10)
    assert (result.code, result.stdout, result.killed) == (0, "끝\n", False)
    assert proc.waits == [psrun._STOP_POLL_SECONDS] and not proc.killed


@pytest.mark.usefixtures("runs")
def test_a_child_that_outlives_kill_does_not_hang_the_call(monkeypatch):
    proc = use(monkeypatch, FakeProc(ends=False, dies=False))
    result = run_ps_template(TEMPLATE, {}, timeout=0.01)
    assert result.killed and result.code is None and proc.waits == [0.01, 10]


def test_the_tree_is_ended_when_anything_fails_after_the_start(monkeypatch, runs):
    proc = use(monkeypatch, FakeProc(ends=False))

    def refuse(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(psrun.threading.Thread, "start", refuse)
    with pytest.raises(RuntimeError):
        run_ps_template(TEMPLATE, {}, timeout=5)
    assert [argv[1:] for argv, _ in runs] == [["/PID", "0", "/T", "/F"]] and proc.killed
    kwargs = runs[0][1]  # taskkill의 출력이 MCP의 표준 출력으로 가지 않는다
    assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
