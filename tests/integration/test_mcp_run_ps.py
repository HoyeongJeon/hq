"""mcp-run.ps1과 run_on_server를 이 PC의 실제 powershell.exe로 확인한다.

서버는 이 PC의 다른 powershell.exe(local_server, HQ_WHO=server)다. 시험은 템플릿의 세션 구역만 바꿔 그 프로세스에 이름
있는 파이프로 세션을 연다. WinRM을 거치지 않고 이 PC 밖으로 나가지 않는다. 진짜 세션 구역을 그대로 두는 시험은
비밀번호를 읽지 못해 그 구역에 닿기 전에 끝나는 것 하나뿐이다.
"""

import shutil
import threading
import time
from pathlib import Path

import pytest
from pipesession import pipe_session
from ps import parse_errors, run_ps

from hq.mcp.runner import RunOutcome, ServerJob, run_on_server
from hq.secret_store import SecretStore

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
PS_DIR = ROOT / "src" / "hq" / "ps"
TEMPLATE = PS_DIR / "mcp-run.ps1"
SESSION_START = "#region session"
SESSION_END = "#endregion session"
SERVER_ID = "0123456789abcdef0123456789abcdef"
PASSWORD = "pw-PROBE-456"  # 지어낸 비밀번호. 비밀번호 파일과 가짜 자격 증명에 함께 쓴다
# 진짜 세션 구역 대신 local_server에 세션을 열고, 템플릿의 $cred를 가짜 자격 증명으로 둔다
PIPE = pipe_session("$session") + (
    "$cred = New-Object System.Management.Automation.PSCredential('hq-test', "
    f"(ConvertTo-SecureString '{PASSWORD}' -AsPlainText -Force))\n"
)
# 실행된 쪽에 ran-server.txt(서버) 또는 ran-client.txt(템플릿을 돌린 이 PC의 powershell.exe)를 남긴다
MARK = 'New-Item -ItemType File -Path (Join-Path $env:HQ_DIR "ran-$env:HQ_WHO.txt") | Out-Null'
BLOCKED_LINE = "스크립트가 입력을 기다려 멈췄습니다(확인 질문, Read-Host 등). 원격 실행에서는 답할 수 없습니다."


def template_copy(folder: Path, session: str) -> Path:
    """mcp-run.ps1과 bootstrap.ps1을 folder에 복사하고, 사본의 세션 구역을 session 글자로 바꾼다."""
    text = TEMPLATE.read_text(encoding="utf-8")
    start = text.index(SESSION_START)
    end = text.index(SESSION_END, start) + len(SESSION_END)
    copy = text[:start] + session + text[end:]
    assert "New-PSSession" not in copy  # 사본은 어떤 길로도 WinRM 세션을 만들지 않는다
    folder.mkdir(exist_ok=True)
    (folder / "mcp-run.ps1").write_text(copy, encoding="utf-8")
    shutil.copyfile(PS_DIR / "bootstrap.ps1", folder / "bootstrap.ps1")
    return folder / "mcp-run.ps1"


def secret_file(tmp_path: Path) -> Path:
    """hq와 같은 방식(DPAPI, 16진 문자열)으로 tmp_path 안에 쓴 비밀번호 파일."""
    store = SecretStore(tmp_path / "secrets")
    store.save(SERVER_ID, PASSWORD)
    return store.path_for(SERVER_ID)


def run(tmp_path, script, *, session=PIPE, timeout=20, cancel_after=None, edit=None) -> RunOutcome:
    """edit = (옛 글자, 새 글자)면 사본에서 한 곳을 더 바꾼다(템플릿 안을 들여다보는 시험용)."""
    template = template_copy(tmp_path / "template", session)
    if edit is not None:
        text = template.read_text(encoding="utf-8")
        assert text.count(edit[0]) == 1, edit[0]
        template.write_text(text.replace(*edit), encoding="utf-8")
    cancel = tmp_path / "run.cancel"
    cancel.unlink(missing_ok=True)
    job = ServerJob("시험 (10.0.1.21)", "10.0.1.21", "hq-test", secret_file(tmp_path), script, timeout, cancel)
    timer = threading.Timer(cancel_after, cancel.touch) if cancel_after is not None else None
    if timer:
        timer.start()
    try:
        return run_on_server(job, trusted=None, template=template)
    finally:
        if timer:
            timer.cancel()


def test_the_script_runs_on_the_far_side_and_hangul_comes_back(tmp_path, local_server):
    result = run(tmp_path, MARK + "; '한글 ' + $env:HQ_WHO")
    assert result.status == "OK" and result.output.strip() == "한글 server", result
    assert (tmp_path / "ran-server.txt").exists() and not (tmp_path / "ran-client.txt").exists()


@pytest.mark.parametrize(
    "session",
    ["$session = $null\n", "throw 'no session'\n", "", PIPE + "Remove-PSSession $session\n"],
    ids=["null", "throws", "none", "closed"],  # closed: 세션 개체는 있지만 Opened가 아니다
)
def test_without_a_session_nothing_runs_anywhere(tmp_path, local_server, session):
    result = run(tmp_path, MARK, session=session)
    assert result.status != "OK" and list(tmp_path.glob("ran-*.txt")) == [], result


def test_special_characters_run_exactly_as_written(tmp_path, local_server):
    script = "$a = 'it''s'; $b = \"q`\"q\"; \"$a|$b|`$x|한글\"\n'second; line'"
    assert run(tmp_path, script).output.splitlines() == ["it's|q\"q|$x|한글", "second; line"]


# 서버에서 $using: 읽기가 실패하면 그 문장만 끝난다. 그래서 상태는 OK이고 오류는 errors에 있다. 이 시험이 보는 것은
# 비밀번호를 아무도 읽지 못한다는 것이다.
def test_using_cannot_read_the_templates_variables(tmp_path, local_server):
    result = run(tmp_path, "($using:cred).GetNetworkCredential().Password")
    assert result.status == "OK" and result.errors.strip() != "" and result.output.strip() == "", result
    assert "pw-PROBE-456" not in result.output + result.errors


def test_a_syntax_error_is_a_script_error(tmp_path, local_server):
    # 스크립트는 서버에서만 파싱한다. 문법 오류도 서버에서 나서 스크립트 오류가 된다
    result = run(tmp_path, "foo(")
    assert (result.status, result.exit_code) == ("SCRIPT_ERROR", 2) and result.errors.strip() != "", result


def test_the_remote_block_is_sent_as_text_and_never_parsed_here(tmp_path):
    # 원격 실행 명령은 블록을 명령 개체로 바꿔 보려고 블록의 식을 이 PC에서 계산한다(GetPowerShell). 그러면 스크립트가
    # 이 PC에서 파싱된다. 템플릿의 블록은 그 변환이 스크립트를 보기 전에 거절되는 모양이어야 글자 그대로 서버로 간다
    line = next(x for x in TEMPLATE.read_text(encoding="utf-8").splitlines() if "-ScriptBlock {" in x)
    block = line[line.index("-ScriptBlock {") + len("-ScriptBlock {") : line.rindex("}")]
    check = (
        "$b = [scriptblock]::Create($env:HQ_BLOCK)\n"
        "try { $null = $b.GetPowerShell($true, @('foo(')); 'converted' } catch {\n"
        "    $e = $_.Exception; while ($null -ne $e.InnerException) { $e = $e.InnerException }; $e.GetType().Name }\n"
    )
    result = run_ps(check, env={"HQ_BLOCK": block})
    assert result.stdout.split() == ["ScriptBlockToPowerShellNotSupportedException"], result.stdout + result.stderr


def test_a_thrown_error_is_a_script_error_and_a_written_error_is_not(tmp_path, local_server):
    assert run(tmp_path, "throw '망함'").status == "SCRIPT_ERROR"
    written = run(tmp_path, "Write-Error '경고성'; Write-Warning '조심'; Write-Host '호스트'; 'after'")
    assert written.status == "OK" and "경고성" in written.errors, written
    # 스트림 사이의 순서는 정하지 않는다
    assert set(written.output.splitlines()) == {"WARNING: 조심", "호스트", "after"}


def test_a_failing_statement_does_not_stop_the_script(tmp_path, local_server):
    # PowerShell에 붙여 넣은 것과 같다: 문장 하나가 실패해도 다음 문장으로 간다. 스크립트가 멈추게 하면 SCRIPT_ERROR다
    result = run(tmp_path, "$null.Foo(); 'after'")
    assert result.status == "OK" and result.output.strip() == "after" and result.errors.strip() != "", result
    stopped = run(tmp_path, "$ErrorActionPreference = 'Stop'; $null.Foo(); 'after'")
    assert stopped.status == "SCRIPT_ERROR" and "after" not in stopped.output, stopped


def test_progress_records_are_dropped_and_do_not_disturb_the_output(tmp_path, local_server):
    result = run(tmp_path, "1..50 | ForEach-Object { Write-Progress -Activity 'a' -Status \"$_\" }; 'done'")
    assert (result.status, result.output.strip(), result.errors) == ("OK", "done", ""), result


# 마지막으로 기록을 옮긴 뒤 작업 안쪽 모음(Results)에 남은 수를 tmp_path/results-left.txt에 쓴다
RESULTS_LEFT = (
    "    Write-HqStreams\n"
    "    [IO.File]::WriteAllText((Join-Path $env:HQ_DIR 'results-left.txt'), [string][System.Management.Automation.Job]"
    ".GetProperty('Results', [Reflection.BindingFlags]'Instance, NonPublic').GetValue($hqChild).Count)\n"
)


def test_the_job_keeps_no_second_copy_of_the_output(tmp_path, local_server):
    # 원격 작업은 모든 기록을 안쪽 Results 모음에도 쌓는다. 비우지 않으면 메모리가 출력 크기만큼 는다
    edit = ("    Write-HqStreams\n\n    # 7.", RESULTS_LEFT + "\n    # 7.")
    result = run(tmp_path, '1..20000 | ForEach-Object { "line $_" }', edit=edit)
    lines = sum(len(f"line {i}\n") for i in range(1, 20001))
    assert (result.status, result.output_chars, result.errors) == ("OK", lines, ""), (result.status, result.errors)
    assert (tmp_path / "results-left.txt").read_text(encoding="utf-8") == "0"


def test_a_script_that_waits_for_input_is_stopped_at_once(tmp_path, local_server):
    started = time.monotonic()
    result = run(tmp_path, "'before'; Read-Host 'q'; " + MARK, timeout=20)
    assert (result.status, result.exit_code) == ("SCRIPT_ERROR", 2) and time.monotonic() - started < 12, result
    assert "before" in result.output and BLOCKED_LINE in result.errors and list(tmp_path.glob("ran-*.txt")) == []


def test_the_time_limit_stops_the_script_inside_powershell(tmp_path, local_server):
    started = time.monotonic()
    result = run(tmp_path, "'before'; Start-Sleep 60; " + MARK, timeout=5)
    assert result.status == "TIMEOUT" and result.exit_code == 21 and time.monotonic() - started < 14, result
    assert "before" in result.output and "그 전까지는 서버에서 실행됐습니다" in result.note
    assert list(tmp_path.glob("ran-*.txt")) == []


def test_the_cancel_file_stops_it_the_same_way(tmp_path, local_server):
    assert run(tmp_path, "Start-Sleep 60", cancel_after=2).exit_code == 22


def test_a_cancel_ends_the_run_even_when_the_far_side_does_not_stop(tmp_path, local_server, monkeypatch):
    # Stop-Job은 서버의 파이프라인이 실제로 멈출 때까지 기다리고, Thread.Sleep은 멈추라는 요청을 듣지 않는다.
    # 취소 파일이 생긴 뒤 정리 시간(시험에서는 2초)이 지나면 psrun이 프로세스 트리를 끝낸다
    monkeypatch.setattr("hq.mcp.runner.GRACE_SECONDS", 2)
    started = time.monotonic()
    result = run(tmp_path, "[Threading.Thread]::Sleep(20000)", cancel_after=1)
    assert result.status == "CANCELLED" and time.monotonic() - started < 12, result


@pytest.mark.parametrize("content", [None, "not-hex"], ids=["missing", "not-a-secret"])
def test_a_missing_password_file_is_14_and_the_path_is_not_printed(tmp_path, content):
    secret = tmp_path / "비밀번호 없음" / "0123.dpapi"
    if content is not None:
        secret.parent.mkdir()
        secret.write_text(content, encoding="ascii")
    job = ServerJob("시험 (192.0.2.10)", "192.0.2.10", "hq-test", secret, "'x'", 30, tmp_path / "run.cancel")
    # 먼저 세션 구역을 표시로 바꾼 사본으로 그 구역에 닿지 않는 것을 본다. 닿으면 진짜 구역으로는 돌리지 않는다
    reached = "[Console]::Out.WriteLine('reached-session')\n"
    probe = run_on_server(job, trusted=None, template=template_copy(tmp_path / "template", reached))
    assert probe.exit_code == 14 and "reached-session" not in probe.output, probe
    # 진짜 템플릿. 세션 구역에 닿았다면 문서용 주소에 접속을 시도해 몇십 초 걸리고 14가 아닌 코드로 끝난다
    started = time.monotonic()
    result = run_on_server(job, trusted=None)
    assert (result.status, result.exit_code) == ("SECRET_UNREADABLE", 14) and time.monotonic() - started < 10, result
    assert (result.output, result.errors) == ("", "저장된 비밀번호를 읽을 수 없습니다.\n")  # 경로와 예외 글자가 없다


def test_the_session_is_removed_so_the_next_run_can_connect(tmp_path, local_server):
    # 파이프는 한 번에 하나만 받는다. 세션이 남으면 다음 접속이 실패한다
    assert run(tmp_path, "'one'").status == "OK"
    assert run(tmp_path, "Start-Sleep 60", timeout=5).status == "TIMEOUT"
    assert run(tmp_path, "'three'").output.strip() == "three"


def test_a_server_that_dies_during_the_script_is_disconnected(tmp_path, local_server):
    result = run(tmp_path, "'before'; Stop-Process -Id $PID -Force")
    assert (result.status, result.exit_code) == ("DISCONNECTED", 20), result
    assert "before" in result.output and result.errors.strip() != ""  # 끊긴 이유를 표준 오류에 찍었다


def test_the_template_is_valid_51_and_never_runs_a_script_without_a_session(tmp_path):
    text = TEMPLATE.read_text(encoding="utf-8")
    assert parse_errors(text, tmp_path) == []
    assert text.count("Invoke-Command") == text.count("Invoke-Command -Session $session")
    assert "-Authentication Negotiate" in text and "FromBase64String" not in text and "$using:" not in text
    # 비밀번호를 읽지 못해 끝나는 길이 세션 구역보다 앞에 있다
    assert text.index("[Environment]::Exit(14)") < text.index(SESSION_START)
    # 원격 호스트 호출을 이 PC에서 실행하는 Receive-Job은 쓰지 않는다. 연결이 끊긴 작업도 끝난 것으로 본다
    assert "Receive-Job" not in text
    assert "$hqFinished = @('Completed', 'Failed', 'Stopped', 'Disconnected')" in text
