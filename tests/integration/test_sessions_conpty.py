import ctypes
import json
import logging
import subprocess
import sys
import threading
import time
from ctypes import wintypes

import pytest
from fakes import wait_for
from ptyrun import DA1_ANSWER, DA1_QUERY

from hq.bootstrap import NOT_READY_CODE, READY_MARKER, encoded_argv, powershell_exe, prelude
from hq.sessions import SessionManager, spawn_conpty

pytestmark = pytest.mark.integration
SID = "d" * 32
SHELL = [powershell_exe(), "-NoLogo", "-NoProfile", "-NoExit"]


class Traced:
    """ConPty를 감싸, 읽은 출력과 쓴 입력을 일어난 순서대로 trace에 남긴다. 나머지는 그대로 넘긴다."""

    def __init__(self, pty, trace: list[tuple[str, str]]) -> None:
        self._pty = pty
        self.trace = trace

    def __getattr__(self, name):
        return getattr(self._pty, name)

    def read(self, size: int = 4096) -> str:
        data = self._pty.read(size)
        self.trace.append(("out", data))
        return data

    def write(self, s: str) -> int:
        self.trace.append(("in", s))
        return self._pty.write(s)


def make(**options):
    """실제 ConPTY로 세션 관리자를 만든다. 띄운 PTY도 돌려줘 pid와 trace를 볼 수 있게 한다."""
    out, exits, ptys = [], [], []

    def spawn(argv, rows, cols):
        ptys.append(Traced(spawn_conpty(argv, rows, cols), []))
        return ptys[-1]

    def on_output(session_id, data):
        out.append(data)
        if DA1_QUERY in data:  # 앱에서 xterm.js가 하듯 같은 입력 길로 답한다
            manager.write(session_id, DA1_ANSWER)

    manager = SessionManager(spawn=spawn, on_output=on_output, on_exit=lambda s, c: exits.append(c), **options)
    return manager, out, exits, ptys


@pytest.fixture
def cleanup():
    managers = []
    yield managers.append
    for manager in managers:
        manager.close_all()


def pid_alive(pid: int) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def session_warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "hq.sessions" and r.levelno >= logging.WARNING]


def reader_running(session_id: str) -> bool:
    return any(t.name == f"hq-read-{session_id[:8]}" for t in threading.enumerate())


def test_real_powershell_output_and_exit_code(cleanup, caplog):
    sid = "1" * 32
    manager, out, exits, _ = make()
    cleanup(manager)
    argv = [powershell_exe(), "-NoLogo", "-NoProfile", "-Command", "Write-Output '안녕 hq'; exit 7"]
    manager.open(sid, "s", argv, 80, 24)
    assert wait_for(lambda: exits, timeout=20)
    assert exits == [7]
    assert "안녕 hq" in "".join(out)
    # 읽기 스레드는 ConPTY의 늦은 EOF(끝난 뒤 약 5초)를 받고 스스로 끝난다
    assert wait_for(lambda: not reader_running(sid), timeout=10)
    assert session_warnings(caplog) == []  # 늦은 EOF는 오류가 아니다


def test_interactive_input_then_close_within_the_timeout(cleanup, caplog):
    manager, out, exits, ptys = make()
    cleanup(manager)
    manager.open(SID, "s", SHELL, 80, 24)
    manager.write(SID, "Write-Output (40+2)\r")
    assert wait_for(lambda: "42" in "".join(out), timeout=20)
    start = time.monotonic()
    manager.close(SID)
    assert time.monotonic() - start < 3
    time.sleep(0.5)
    assert exits == []  # 닫으려고 끝낸 세션은 보고하지 않는다
    assert not pid_alive(ptys[0].pid)
    assert not reader_running(SID)  # 닫으면서 PTY를 놓아 막혀 있던 읽기도 끝났다
    assert session_warnings(caplog) == []  # cancel_io로 깨운 읽기는 오류가 아니다


def test_close_kills_a_shell_that_ignores_ctrl_c_and_exit(cleanup):
    sid = "2" * 32
    manager, out, exits, ptys = make(close_timeout=1.0)
    cleanup(manager)
    manager.open(sid, "s", SHELL, 80, 24)
    manager.write(sid, "[Console]::TreatControlCAsInput = $true; Write-Output ('busy' + '-now'); Start-Sleep 60\r")
    assert wait_for(lambda: "busy-now" in "".join(out), timeout=20)
    start = time.monotonic()
    manager.close(sid)
    assert 1.0 <= time.monotonic() - start < 3
    assert not pid_alive(ptys[0].pid)
    assert ptys[0].exitstatus == 15  # terminate()의 TerminateProcess가 끝냈다(PTY를 놓아서 끝난 것이 아니다)
    assert wait_for(lambda: not reader_running(sid), timeout=1)
    time.sleep(0.3)
    assert exits == []


def _ignore_ctrl_c_like_a_new_process_group() -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetConsoleCtrlHandler.argtypes = [ctypes.c_void_p, wintypes.BOOL]
    assert kernel32.SetConsoleCtrlHandler(None, True)


def test_ctrl_c_stops_a_running_command_even_if_hq_ignores_ctrl_c(cleanup):
    # hq가 Ctrl+C를 무시하는 상태로 시작됐다고 친다(예: CREATE_NEW_PROCESS_GROUP으로 띄움). 자식은 이 상태를 물려받는다.
    _ignore_ctrl_c_like_a_new_process_group()
    sid = "3" * 32
    manager, out, _, _ = make()
    cleanup(manager)
    manager.open(sid, "s", SHELL, 80, 24)
    manager.write(sid, "Write-Output ('sleep' + 'ing'); Start-Sleep 30\r")
    assert wait_for(lambda: "sleeping" in "".join(out), timeout=20)
    before = len(out)
    manager.write(sid, "\x03")
    assert wait_for(lambda: "PS " in "".join(out[before:]), timeout=2)  # Start-Sleep이 멈추고 프롬프트가 돌아왔다
    # Ctrl+C와 거의 같은 때 보낸 입력은 버려진다(실측). 그래서 다음 명령은 프롬프트가 돌아온 뒤에 친다.
    manager.write(sid, "Write-Output ('after' + '-ctrl-c')\r")
    assert wait_for(lambda: "after-ctrl-c" in "".join(out), timeout=5)


NO_LISTEN_HELPER = r"""
import json, os, socket, subprocess
from hq.bootstrap import powershell_exe
from hq.sessions import spawn_conpty

pty = spawn_conpty([powershell_exe(), "-NoLogo", "-NoProfile", "-NoExit"], 24, 80)
pty.read()  # 프로세스가 떠서 출력을 내기 시작했다
control = socket.create_server(("127.0.0.1", 0))  # 대조군: 이 확인 방법이 LISTEN 포트를 실제로 찾아내는지
query = (
    "Get-NetTCPConnection -State Listen -OwningProcess %d -ErrorAction SilentlyContinue"
    " | ForEach-Object { $_.LocalPort }" % os.getpid()
)
found = subprocess.run(
    [powershell_exe(), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", query],
    capture_output=True, text=True, check=True,
).stdout.split()
print(json.dumps({"control": control.getsockname()[1], "listening": sorted(int(p) for p in found)}))
control.close()
pty.terminate(force=True)
pty.close()
"""


def test_a_program_that_cannot_start_raises_oserror(tmp_path):
    # 백신이 실행을 막을 때처럼 CreateProcess가 실패하면 winpty는 WinptyError를 던진다. 창구(bridge)가 한 가지로
    # 받아 안내하도록 OSError로 바꿔 던진다(원래 메시지는 남긴다).
    fake = tmp_path / "not-a-program.exe"
    fake.write_text("이건 실행 파일이 아니다", encoding="utf-8")
    with pytest.raises(OSError, match=r"\S"):
        spawn_conpty([str(fake)], 24, 80)


def test_a_session_leaves_hq_without_a_listening_port():
    # 다른 테스트가 남긴 소켓이 섞이지 않게 새 프로세스에서 띄우고, 그 프로세스의 LISTEN 포트만 본다.
    result = subprocess.run(
        [sys.executable, "-c", NO_LISTEN_HELPER], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.splitlines()[-1])
    assert report["listening"] == [report["control"]]


# ---- 준비 신호 (spec 7.3) ----


def first_output_with(trace: list[tuple[str, str]], text: str) -> int:
    """출력을 이어 붙여 text가 처음 다 보인 trace 위치."""
    seen = ""
    for index, (kind, data) in enumerate(trace):
        if kind == "out":
            seen += data
            if text in seen:
                return index
    raise AssertionError(f"출력에 {text!r}가 없다: {seen[-300:]!r}")


def test_the_real_prelude_opens_the_gate_and_held_input_runs_after_it(cleanup):
    sid = "4" * 32
    manager, out, exits, ptys = make(ready_marker=READY_MARKER)
    cleanup(manager)
    # 원격 세션 대신 한 줄을 읽어 바꿔 찍는다. 그 뒤 스크립트가 끝나 프롬프트를 그리려 하면 안전장치가 1로 끝낸다.
    script = prelude() + "$line = Read-Host\nWrite-Output ('got-' + $line.ToUpper())\n"
    manager.open(sid, "s", encoded_argv(script), 80, 24)
    manager.write(sid, "hello\r")  # 신호가 오기 전에 친 입력
    assert wait_for(lambda: "got-HELLO" in "".join(out), timeout=20)
    assert wait_for(lambda: exits == [1], timeout=10)
    trace = ptys[0].trace
    ready_at = first_output_with(trace, READY_MARKER)
    # DA1 응답은 on_output이 manager.write로만 보냈다. 문을 지나 신호보다 먼저 갔고, 친 입력은 신호 뒤에야 갔다.
    assert trace.index(("in", DA1_ANSWER)) < ready_at < trace.index(("in", "hello\r"))


def never_ready_argv(kind: str) -> list[str]:
    if kind == "blocked":
        # 백신이 본문을 막은 것처럼: 안전장치도 신호도 없이 -NoExit가 내 PC의 프롬프트를 연다
        return [*SHELL, "-Command", "Start-Sleep 0"]
    # 정책이 제한 언어 모드를 건 것처럼: 진짜 prelude가 돌지만 [Environment]::Exit와 제목 바꾸기가 거부된다.
    # 안전장치가 듣지 않아 내 PC의 프롬프트가 뜬다. 거부 메시지는 제목 줄의 원문을 보여 준다.
    return encoded_argv("$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'\n" + prelude())


NEVER_READY = ["blocked", "constrained-language"]


def typed_ahead(flag) -> str:
    return f"New-Item -ItemType File -Path '{flag}' | Out-Null\r"


@pytest.mark.parametrize("kind", NEVER_READY)
def test_a_shell_that_never_signals_ready_is_ended_and_held_input_never_runs(cleanup, caplog, tmp_path, kind):
    sid = "5" * 32
    flag = tmp_path / "ran-locally.txt"
    manager, out, exits, ptys = make(ready_marker=READY_MARKER, ready_timeout=2.0)
    cleanup(manager)
    start = time.monotonic()
    manager.open(sid, "s", never_ready_argv(kind), 80, 24)
    manager.write(sid, typed_ahead(flag))
    assert wait_for(lambda: exits, timeout=10)
    assert 2.0 <= time.monotonic() - start < 6
    assert exits == [NOT_READY_CODE]
    assert not pid_alive(ptys[0].pid)
    assert not flag.exists(), "".join(out)
    assert any(sid[:8] in message for message in session_warnings(caplog))


@pytest.mark.parametrize("kind", NEVER_READY)
def test_control_without_the_gate_the_same_input_runs_on_this_pc(cleanup, tmp_path, kind):
    # 대조군: 위 테스트와 같은 셸, 같은 입력. 문이 없으면 친 명령이 내 PC에서 실제로 실행된다.
    # 그래서 위 테스트의 "실행되지 않았다"는 그 셸이 원래 입력을 읽지 않아서 나온 결과일 수 없다.
    sid = "6" * 32
    flag = tmp_path / "ran-locally.txt"
    manager, out, _, _ = make()
    cleanup(manager)
    manager.open(sid, "s", never_ready_argv(kind), 80, 24)
    manager.write(sid, typed_ahead(flag))
    assert wait_for(flag.exists, timeout=15), "".join(out)


HELPER = """
import time
from hq.bootstrap import powershell_exe
from hq.sessions import spawn_conpty
pty = spawn_conpty([powershell_exe(), "-NoLogo", "-NoProfile", "-NoExit"], 24, 80)
print(pty.pid, flush=True)
time.sleep(60)
"""


def test_children_end_when_hq_is_killed():
    helper = subprocess.Popen([sys.executable, "-c", HELPER], stdout=subprocess.PIPE, text=True)
    child = int(helper.stdout.readline())
    assert pid_alive(child)
    helper.kill()
    helper.wait()
    assert wait_for(lambda: not pid_alive(child), timeout=5)
