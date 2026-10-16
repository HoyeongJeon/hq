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

from hq.bootstrap import powershell_exe
from hq.sessions import SessionManager, spawn_conpty

pytestmark = pytest.mark.integration
SID = "d" * 32
SHELL = [powershell_exe(), "-NoLogo", "-NoProfile", "-NoExit"]


def make(**options):
    """실제 ConPTY로 세션 관리자를 만든다. 띄운 PTY도 돌려줘 pid를 볼 수 있게 한다."""
    out, exits, ptys = [], [], []

    def spawn(argv, rows, cols):
        ptys.append(spawn_conpty(argv, rows, cols))
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


def test_a_session_leaves_hq_without_a_listening_port():
    # 다른 테스트가 남긴 소켓이 섞이지 않게 새 프로세스에서 띄우고, 그 프로세스의 LISTEN 포트만 본다.
    result = subprocess.run(
        [sys.executable, "-c", NO_LISTEN_HELPER], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.splitlines()[-1])
    assert report["listening"] == [report["control"]]


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
