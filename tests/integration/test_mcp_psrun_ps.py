"""hq.mcp.psrun을 이 PC의 실제 powershell.exe로 확인한다. 템플릿은 시험이 tmp_path에 쓰는 작은 파일이고, 원격이나
네트워크 명령은 쓰지 않는다."""

import subprocess
import threading
import time
from pathlib import Path

import pytest
from procs import end_tree, pid_alive

from hq.mcp.psrun import LOADER_FAILED, loader_argv, run_ps_template

pytestmark = pytest.mark.integration
READ = "$p = [Console]::In.ReadToEnd() | ConvertFrom-Json\n"


def template(tmp_path: Path, body: str) -> Path:
    """BOM 없는 UTF-8로 쓴다. 경로의 한글과 공백도 시험한다."""
    path = tmp_path / "한글 폴더" / "t.ps1"
    path.parent.mkdir(exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def end_grandchild(tmp_path):
    """시험이 실패해도 템플릿이 띄운 손자 프로세스를 남기지 않는다. 템플릿은 그 pid를 tmp_path / "child.pid"에 쓴다."""
    yield
    pid_file = tmp_path / "child.pid"
    pid = pid_file.read_text().strip() if pid_file.exists() else ""
    if pid.isdigit():
        end_tree(int(pid))


@pytest.fixture
def started(monkeypatch):
    """run_ps_template가 띄운 프로세스를 모은다. 시험이 실패하거나 호출이 돌아오지 않아도 끝에 트리째 끝낸다."""
    procs = []
    real = subprocess.Popen

    def record(argv, **kwargs):
        procs.append(real(argv, **kwargs))
        return procs[-1]

    monkeypatch.setattr("hq.mcp.psrun.subprocess.Popen", record)
    yield procs
    for proc in procs:
        end_tree(proc.pid)


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
    assert "\ufffd" not in result.stdout + result.stderr


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


def test_a_template_that_never_reads_its_input_is_still_ended_by_the_timeout(tmp_path, started):
    # 값은 ASCII JSON으로 120,000바이트라 파이프 버퍼보다 크다. 템플릿이 읽지 않아 쓰기가 막혀도 timeout은 지킨다.
    # 호출을 따로 돌려, 돌아오지 않아도 시험이 멈추지 않고 started가 프로세스를 끝내게 한다
    results = []
    call = threading.Thread(
        target=lambda: results.append(
            run_ps_template(template(tmp_path, "Start-Sleep 60\n"), {"script": "가" * 20_000}, timeout=4)
        ),
        daemon=True,
    )
    call.start()
    call.join(15)
    assert not call.is_alive(), "timeout이 지나도 15초 안에 돌아오지 않았다"
    assert results[0].killed


def test_powershells_own_records_are_removed_and_the_lines_around_them_kept(tmp_path):
    # [Environment]::Exit 없이 끝나야 PowerShell이 #< CLIXML 줄 뒤에 <Objs> 덩어리까지 쓴다
    body = (
        READ
        + "[Console]::Error.WriteLine('앞')\n"
        + "Write-Error x -ErrorAction Continue\n"
        + "Write-Warning y\n"
        + "[Console]::Error.WriteLine('뒤')\n"
    )
    result = run_ps_template(template(tmp_path, body), {}, timeout=30)
    assert result.stderr.splitlines() == ["앞", "뒤"]
    assert "CLIXML" not in result.stderr and "<Objs" not in result.stderr
    assert result.total_chars == len("앞\n뒤\n")  # 지운 글자는 세지 않는다


def test_records_never_interleave_with_the_templates_own_error_lines(tmp_path):
    # PowerShell은 기록의 <Objs> 덩어리를 약 6,000자마다 아무 데서나 끊어 쓴다. 기록이 표준 오류에 가면 그 사이에
    # 쓴 줄이 덩어리 안에 끼어 함께 지워진다. 로더가 모든 스트림을 버려 기록이 아예 가지 않게 한다
    body = READ + 'for ($i = 1; $i -le 300; $i++) { Write-Error "e$i"; [Console]::Error.WriteLine("plain$i") }\n'
    result = run_ps_template(template(tmp_path, body), {}, timeout=60)
    assert result.stderr.splitlines() == [f"plain{i}" for i in range(1, 301)]
    assert "CLIXML" not in result.stderr and "<Objs" not in result.stderr


def test_leaked_pipeline_output_warnings_and_host_text_reach_neither_stream(tmp_path):
    body = (
        READ
        + "'leaked-output'\n"
        + "Write-Warning 'leaked-warning'\n"
        + "Write-Host 'leaked-host'\n"
        + "[Console]::Out.WriteLine('plain')\n"
    )
    result = run_ps_template(template(tmp_path, body), {}, timeout=30)
    assert (result.stdout, result.stderr) == ("plain\n", "")


def test_exit_inside_the_template_still_sets_the_exit_code(tmp_path):
    body = READ + "Write-Error x\n[Console]::Out.WriteLine('before exit')\n[Environment]::Exit(3)\n"
    result = run_ps_template(template(tmp_path, body), {}, timeout=30)
    assert (result.code, result.stdout, result.stderr, result.killed) == (3, "before exit\n", "", False)


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (READ + "throw 'secret-PROBE'\n", "System.Management.Automation.RuntimeException, 줄 2"),
        (READ + "Write-Error x\nthrow 'secret-PROBE'\n", "System.Management.Automation.RuntimeException, 줄 3"),
        # 로더의 5째 줄이 템플릿을 읽어 스크립트 블록을 만든다. 문법 오류와 없는 파일은 그 줄에서 난다
        (READ + "if ('secret-PROBE' {\n", "System.Management.Automation.MethodInvocationException, 줄 5"),
        (None, "System.Management.Automation.MethodInvocationException, 줄 5"),
    ],
    ids=["throws", "throws-after-records", "syntax-error", "missing-file"],
)
def test_a_template_that_fails_to_load_or_throws_ends_with_one_plain_line(tmp_path, body, error):
    path = template(tmp_path, body) if body is not None else tmp_path / "secret-PROBE 없음.ps1"
    result = run_ps_template(path, {"secret": "secret-PROBE"}, timeout=30)
    assert result.code == LOADER_FAILED and not result.killed
    assert result.stderr == f"템플릿을 실행하지 못했습니다 ({error})\n"  # 예외 메시지는 값을 옮길 수 있어 넣지 않는다
    assert "secret-PROBE" not in result.stdout + result.stderr and "CLIXML" not in result.stderr


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
