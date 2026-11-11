"""hq.mcp.psrun을 이 PC의 실제 powershell.exe로 확인한다. 템플릿은 시험이 tmp_path에 쓰는 작은 파일이고, 원격이나
네트워크 명령은 쓰지 않는다."""

import ctypes
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

import pytest

from hq.mcp.psrun import loader_argv, run_ps_template

pytestmark = pytest.mark.integration
READ = "$p = [Console]::In.ReadToEnd() | ConvertFrom-Json\n"


def template(tmp_path: Path, body: str) -> Path:
    """BOM 없는 UTF-8로 쓴다. 경로의 한글과 공백도 시험한다."""
    path = tmp_path / "한글 폴더" / "t.ps1"
    path.parent.mkdir(exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


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


@pytest.fixture
def end_grandchild(tmp_path):
    """시험이 실패해도 템플릿이 띄운 손자 프로세스를 남기지 않는다. 템플릿은 그 pid를 tmp_path / "child.pid"에 쓴다."""
    yield
    pid_file = tmp_path / "child.pid"
    pid = pid_file.read_text().strip() if pid_file.exists() else ""
    if pid.isdigit() and pid_alive(int(pid)):
        subprocess.run(
            ["taskkill", "/PID", pid, "/T", "/F"],
            capture_output=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )


def test_the_payload_arrives_exactly_including_hangul_quotes_and_20000_characters(tmp_path):
    script = "한글 ' \" $x ` ; \n" + "x" * 19_980
    t = template(
        tmp_path,
        READ
        + "[Console]::Out.WriteLine($p.script.Length); [Console]::Out.WriteLine($p.script.Substring(0, 14))\n"
        + "[Environment]::Exit(7)\n",
    )
    result = run_ps_template(t, {"script": script}, timeout=30)
    assert (
        result.code == 7 and result.stdout.splitlines() == [str(len(script)), "한글 ' \" $x ` ; "] and not result.killed
    )


def test_megabytes_on_both_streams_do_not_block_and_only_40000_characters_are_kept(tmp_path):
    body = (
        READ
        + "$line = '가' * 1000\n"
        + "for ($i = 0; $i -lt 3000; $i++) { [Console]::Out.WriteLine($line); [Console]::Error.WriteLine($line) }\n"
        + "[Environment]::Exit(0)\n"
    )
    result = run_ps_template(template(tmp_path, body), {}, timeout=60)
    assert result.code == 0 and len(result.stdout) + len(result.stderr) == 40_000
    assert result.total_chars == 2 * 3000 * 1001  # 줄바꿈은 한 글자로 센다(\r\n → \n)
    assert "�" not in result.stdout + result.stderr


@pytest.mark.usefixtures("end_grandchild")
def test_a_script_that_runs_too_long_is_killed_with_its_children(tmp_path):
    pid_file = tmp_path / "child.pid"
    body = (
        READ
        + "$c = Start-Process powershell -ArgumentList '-NoProfile','-Command','Start-Sleep 60' -WindowStyle Hidden"
        + " -PassThru\n"
        + "[IO.File]::WriteAllText($p.pid_file, $c.Id)\n"
        + "Start-Sleep 60\n"
    )
    started = time.monotonic()
    result = run_ps_template(template(tmp_path, body), {"pid_file": str(pid_file)}, timeout=4)
    assert result.killed and result.code is None and time.monotonic() - started < 15
    assert not pid_alive(int(pid_file.read_text()))  # 손자 프로세스도 끝났다


def test_powershells_own_error_stream_marker_is_cut_off(tmp_path):
    body = READ + "[Console]::Error.WriteLine('오류 줄')\nWrite-Error 'x'\n[Environment]::Exit(1)\n"
    result = run_ps_template(template(tmp_path, body), {}, timeout=30)
    assert result.stderr.strip() == "오류 줄"


def test_the_command_line_holds_no_script_and_no_payload(tmp_path):
    argv = loader_argv(tmp_path / "t.ps1")
    assert argv[1:4] == ["-NoLogo", "-NoProfile", "-NonInteractive"] and argv[4] == "-EncodedCommand" and len(argv) == 6


def test_the_child_gets_its_own_three_pipes_and_no_window(tmp_path, monkeypatch):
    seen = {}
    real = subprocess.Popen

    def spy(argv, **kwargs):
        seen.update(kwargs)
        return real(argv, **kwargs)

    monkeypatch.setattr("hq.mcp.psrun.subprocess.Popen", spy)
    run_ps_template(template(tmp_path, READ + "[Environment]::Exit(0)\n"), {}, timeout=30)
    assert seen["stdin"] == seen["stdout"] == seen["stderr"] == subprocess.PIPE  # MCP의 표준 입출력을 물려받지 않는다
    assert seen["creationflags"] & subprocess.CREATE_NO_WINDOW
