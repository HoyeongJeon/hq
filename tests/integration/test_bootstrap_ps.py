import functools
import re

import pytest
from ps import parse_errors, run_ps
from ptyrun import run_pty

from hq import bootstrap
from hq.bootstrap import (
    READY_MARKER,
    BootstrapParams,
    build_command,
    build_script,
    encoded_argv,
    prelude,
    ps_json_literal,
)

pytestmark = pytest.mark.integration

PARAMS = BootstrapParams("10.0.1.21", "Administrator", "웹01", r"C:\no\such\file.dpapi")
# 값 안의 ':'는 \u003a로 들어간다(준비 신호가 값에서 통째로 나오지 못하게). 그래도 PowerShell이 그대로 읽어야 한다.
NASTY = "웹01'; Remove-Item C:\\ -Recurse; \"$x`;\n\u2018\u2019\u201a\u201b 🙂 " + READY_MARKER
KOREAN_PATH = r"C:\Users\전호영\한글 경로\0123.dpapi"


def test_powershell_reads_the_json_literal_back():
    script = (
        f"$v = {ps_json_literal({'nickname': NASTY, 'path': KOREAN_PATH})} | ConvertFrom-Json\n"
        "Write-Output ($v.nickname -ceq $env:HQ_NICK)\n"
        "Write-Output ($v.path -ceq $env:HQ_PATH)\n"
    )
    result = run_ps(script, env={"HQ_NICK": NASTY, "HQ_PATH": KOREAN_PATH})
    assert result.stdout.split() == ["True", "True"], result.stderr


def test_bootstrap_reads_back_every_parameter():
    params = BootstrapParams("10.0.1.21", r".\Administrator", NASTY, KOREAN_PATH)
    line = next(x for x in build_script(params).splitlines() if x.startswith("$p = "))
    fields = ("host", "username", "nickname", "secret_path")
    checks = "".join(f"Write-Output ($p.{name} -ceq $env:HQ_{name.upper()})\n" for name in fields)
    result = run_ps(line + "\n" + checks, env={f"HQ_{name.upper()}": getattr(params, name) for name in fields})
    assert result.stdout.split() == ["True"] * 4, result.stderr


# Ctrl+C가 안전장치의 Remove-PSSession 도중에 온 것처럼: PipelineStoppedException은 catch를 건너뛰고 finally만 돈다.
STOPPED_CLEANUP = (
    "function global:Remove-PSSession { throw (New-Object System.Management.Automation.PipelineStoppedException) }\n"
)


def _safety(state: str | None, extra: str = "") -> str:
    setup = "" if state is None else f"$global:__hq_session = [pscustomobject]@{{ State = '{state}' }}\n"
    return prelude() + extra + setup + "Start-Sleep -Milliseconds 1500\n"


def test_bootstrap_parses_without_errors(tmp_path):
    assert parse_errors(build_script(PARAMS), tmp_path) == []


@pytest.mark.parametrize("extra", ["", STOPPED_CLEANUP], ids=["normal", "cleanup-stopped"])
def test_broken_session_exits_20_and_typed_ahead_command_never_runs(tmp_path, extra):
    marker = tmp_path / "ran-locally.txt"
    typed = f"New-Item -ItemType File -Path '{marker}' | Out-Null\r"
    output, code = run_pty(encoded_argv(_safety("Broken", extra)), writes=[(0.3, typed)])
    assert code == 20, output
    assert not marker.exists()


def test_control_without_the_safety_net_the_typed_ahead_command_runs(tmp_path):
    # 대조군: 위 테스트와 같은 도구, 같은 입력. 안전장치 대신 그냥 돌아오는 prompt를 두면 미리 친 명령이
    # 실제로 실행된다. 그래서 위 테스트의 "실행되지 않았다"가 입력이 전달되지 않아서 나온 결과일 수 없다.
    marker = tmp_path / "ran-locally.txt"
    typed = f"New-Item -ItemType File -Path '{marker}' | Out-Null\r"
    script = "function global:prompt { 'PS> ' }\nStart-Sleep -Milliseconds 1500\n"
    output, code = run_pty(encoded_argv(script), writes=[(0.3, typed), (0.4, "exit\r")])
    assert code == 0, output
    assert marker.exists(), output


def test_opened_session_means_the_user_left_and_exits_0():
    output, code = run_pty(encoded_argv(_safety("Opened")))
    assert code == 0, output


def test_no_session_exits_1():
    output, code = run_pty(encoded_argv(_safety(None)))
    assert code == 1, output


def test_unreadable_secret_exits_14_before_any_connection(tmp_path):
    bad = tmp_path / "bad.dpapi"
    bad.write_text("not-hex", encoding="ascii")
    params = BootstrapParams("192.0.2.10", "Administrator", "시험", str(bad))
    output, code = run_pty(build_command(params), timeout=40)
    assert code == 14, output


def test_missing_secret_exits_14(tmp_path):
    params = BootstrapParams("192.0.2.10", "Administrator", "시험", str(tmp_path / "missing.dpapi"))
    output, code = run_pty(build_command(params), timeout=40)
    assert code == 14, output


# ---- 준비 신호 (spec 7.3) ----

# 터미널 제목 시퀀스(OSC): ESC ] <번호> ; <제목> 뒤에 BEL 또는 ST(ESC \). 화면에 글자로 찍히지 않는다.
_TITLE = re.compile(r"\x1b\][0-9]*;[^\x07\x1b]*(?:\x07|\x1b\\)")


def _visible(output: str) -> str:
    return _TITLE.sub("", output)


def test_the_ready_signal_reaches_the_conpty_output_as_a_title():
    output, code = run_pty(encoded_argv(prelude()))
    assert code == 1, output  # 세션 없이 프롬프트를 그리려 하자 안전장치가 끝냈다
    assert READY_MARKER in output, repr(output)
    assert READY_MARKER not in _visible(output), repr(output)


def test_the_bootstrap_signals_ready_before_anything_else_and_only_as_a_title(tmp_path):
    params = BootstrapParams("192.0.2.10", "Administrator", "시험", str(tmp_path / "missing.dpapi"))
    output, code = run_pty(build_command(params), timeout=40)
    assert code == 14, output
    assert READY_MARKER in output, repr(output)
    assert output.index(READY_MARKER) < output.index("접속 중"), repr(output)
    assert READY_MARKER not in _visible(output), repr(output)  # 부트스트랩의 다른 출력에는 나오지 않는다


# (FullyQualifiedErrorId, 메시지, 기대 종료 코드). 실서버 표본(V3)은 docs/verification.md에 기록했다.
CASES = [
    ("AccessDenied,PSSessionOpenFailed", "액세스가 거부되었습니다.", 10),
    ("ServerNotTrusted,PSSessionOpenFailed", "The WinRM client cannot process the request. TrustedHosts", 12),
    ("-2144108060,PSSessionOpenFailed", "This user is allowed a maximum number of 5 concurrent shells", 13),
    ("CannotConnect,PSSessionOpenFailed", "WinRM cannot complete the operation.", 11),
    ("WinRMOperationTimeout,PSSessionOpenFailed", "The operation has timed out.", 11),
    ("ComputerNotFound,PSSessionOpenFailed", "The network path was not found.", 11),
    ("SomethingElse,PSSessionOpenFailed", "알 수 없음", 1),
    # V3 실서버 표본 (2026-10-16, SRV-B, 서버 OS 확인하지 않음). 메시지는 보고서 그대로이고, 서버 주소만
    # 문서용 주소로 바꿨다(docs/verification.md).
    # 틀린 비밀번호
    (
        "AccessDenied,PSSessionOpenFailed",
        "다음 오류 때문에 원격 서버 198.51.100.20에 연결하지 못했습니다. 액세스가 거부되었습니다. "
        "자세한 내용은 about_Remote_Troubleshooting 도움말 항목을 참조하십시오.",
        10,
    ),
    # TrustedHosts 없음 (ErrorCode 1312, Negotiate 인증)
    (
        "1312,PSSessionOpenFailed",
        "다음 오류 때문에 원격 서버 192.0.2.10에 연결하지 못했습니다. WinRM cannot process the request. "
        "The following error with error code 0x8009030e occurred while using Negotiate authentication: "
        "A specified logon session does not exist. It may already have been terminated. "
        "This can occur if the provided credentials are not valid on the target server, "
        "or if the server identity could not be verified. If you trust the server identity, "
        "add the server name to the TrustedHosts list, and then retry the request. "
        "Use winrm.cmd to view or edit the TrustedHosts list. "
        "Note that computers in the TrustedHosts list might not be authenticated. "
        "For more information about how to edit the TrustedHosts list, run the following command: winrm help config. "
        "자세한 내용은 about_Remote_Troubleshooting 도움말 항목을 참조하십시오.",
        12,
    ),
    # 포트 닫힘 (ErrorCode -2144108250)
    (
        "WinRMOperationTimeout,PSSessionOpenFailed",
        "다음 오류 때문에 원격 서버 198.51.100.20에 연결하지 못했습니다. WinRM에서 작업을 완료할 수 없습니다. "
        "지정한 컴퓨터 이름이 올바른지, 네트워크를 통해 컴퓨터에 액세스할 수 있는지, "
        "WinRM에 방화벽 예외를 사용하도록 설정하고 이 컴퓨터의 액세스를 허용하는지 확인하십시오. "
        "기본적으로 공용 프로필에 대한 WinRM 방화벽 예외에서는 동일한 로컬 서브넷 내의 원격 컴퓨터로 "
        "액세스를 제한합니다. 자세한 내용은 about_Remote_Troubleshooting 도움말 항목을 참조하십시오.",
        11,
    ),
    # 규칙 순서 (합성 표본). FQID가 정하고, 메시지는 FQID로 정해지지 않을 때만 본다.
    ("1312,PSSessionOpenFailed", "지정된 로그온 세션이 없습니다.", 12),
    ("WinRMOperationTimeout,PSSessionOpenFailed", "add the server name to the TrustedHosts list", 11),
    ("SomethingElse,PSSessionOpenFailed", "add the server name to the TrustedHosts list", 12),
    # 한도 초과는 표본이 없어 FQID를 모른다. 그래서 메시지로 다른 규칙보다 먼저 본다.
    ("AccessDenied,PSSessionOpenFailed", "This user is allowed a maximum number of 5 concurrent shells", 13),
]


def test_error_classification():
    lines = [prelude()]
    for fqid, message, _ in CASES:
        quoted = message.replace("'", "''")
        lines.append(
            "$e = New-Object System.Management.Automation.ErrorRecord("
            f"(New-Object System.Exception('{quoted}')), '{fqid}', 'OpenError', $null)"
        )
        lines.append("Write-Output (Get-HqExitCode $e)")
    result = run_ps("\n".join(lines))
    assert [int(x) for x in result.stdout.split()] == [code for *_, code in CASES], result.stderr


# ---- Tab으로 회색 미리보기 받기 (Task 16b, 규칙 R16b-1) ----
# 탭과 같은 방식(-NoLogo -NoProfile -NoExit -EncodedCommand)으로 이 PC의 대화형 PowerShell을 ConPTY에 띄우고,
# 부트스트랩에 실제로 든 Tab 키 설정 부분(tab_binding)을 돌린 뒤 키를 친다. 어디에도 접속하지 않는다.


# 모두 이 시험 프로세스 안에서만 바뀐다. SaveNothing이면 PSReadLine은 사용자의 기록 파일을 읽지도 쓰지도 않는다.
# PSReadLine은 첫 입력 줄을 읽을 때에야 기록을 만들므로, 그 전(스크립트 안)에 AddToHistory를 부르면
# NullReferenceException이 난다. 그래서 기록 넣기는 함수로 두고 첫 입력 줄로 부른다. 이 줄에는 기대하는 출력
# 글자가 없고, 'Write-Output'으로 시작하지 않아 미리보기 후보도 되지 않는다.
def _tab_setup(history: str = "Write-Output hq-tab-accepted") -> str:
    return (
        "Set-PSReadLineOption -HistorySaveStyle SaveNothing -PredictionSource History\n"
        "function global:Add-HqTestHistory {\n"
        f"    [Microsoft.PowerShell.PSConsoleReadLine]::AddToHistory({_ps_quote(history)})\n"
        "}\n"
        "function global:prompt { 'PS> ' }\n"
        "$hqvar = 'tab-completed'\n"
        "$hqvar2 = 'tab-cycled'\n"
    )


SEED = "Add-HqTestHistory\r"
TAB = "\t"
CTRL_SPACE = "\x00"  # xterm.js가 Ctrl+Space로 보내는 글자
LEFT = "\x1b[D"
# 회색 미리보기가 그려진 흔적: 친 글자(hq-t) 바로 뒤에 색을 바꾸는 시퀀스가 오고 미리보기의 나머지가 이어진다.
_SUGGESTION_SHOWN = re.compile(r"hq-t(?:\x1b\[[0-9;]*m)+ab-accepted")
_CSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _printed_lines(output: str) -> list[str]:
    """제어 시퀀스를 걷어 낸 화면의 줄들. 명령이 찍은 줄은 프롬프트(PS>) 없이 그 글자만 남는다."""
    return [line.strip() for line in _CSI.sub("", _visible(output)).split("\r\n")]


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


@functools.cache
def _psreadline_can_accept_suggestions() -> bool:
    script = (
        "Import-Module PSReadLine\n"
        "Write-Output ($null -ne [Microsoft.PowerShell.PSConsoleReadLine].GetMethod('AcceptSuggestion'))\n"
    )
    return run_ps(script).stdout.split() == ["True"]


@pytest.fixture
def needs_accept_suggestion():
    if not _psreadline_can_accept_suggestions():
        pytest.skip("이 PC의 PSReadLine에 AcceptSuggestion이 없다(2.1보다 낮다)")


def _type_in_a_tab(
    tmp_path,
    typed: str,
    keys: list[str],
    *,
    binding: bool = True,
    history: str = "Write-Output hq-tab-accepted",
    files: tuple[str, ...] = ("hq-tab-completed.txt",),  # 기본은 'hq-t'의 자동완성 후보가 이것 하나
) -> tuple[str, list[str]]:
    """기록에 history 한 줄을 넣고, 작업 폴더에 files를 만든 뒤 typed를 치고 keys를 하나씩 누르고 Enter, exit."""
    for name in files:
        (tmp_path / name).write_text("", encoding="ascii")
    script = (
        "$ErrorActionPreference = 'Stop'\n"  # 부트스트랩과 같다
        + _tab_setup(history)
        + f"Set-Location -LiteralPath {_ps_quote(str(tmp_path))}\n"
        + (bootstrap.tab_binding() if binding else "")
    )
    writes = [(0.5, SEED), (2.0, typed)]
    at = 3.0
    for key in keys:
        writes.append((at, key))
        at += 0.8
    writes += [(at, "\r"), (at + 0.6, "exit\r")]
    output, code = run_pty(encoded_argv(script), writes=writes, timeout=60)
    assert code == 0, output
    return output, _printed_lines(output)


def test_tab_accepts_the_shown_suggestion(tmp_path, needs_accept_suggestion):
    output, lines = _type_in_a_tab(tmp_path, "Write-Output hq-t", [TAB])
    assert _SUGGESTION_SHOWN.search(output), repr(output)
    assert "hq-tab-accepted" in lines, lines


def test_tab_completes_at_the_cursor_when_it_is_not_at_the_end_of_the_line(tmp_path, needs_accept_suggestion):
    # 미리보기는 줄 끝에 붙는다. 커서가 줄 가운데 있으면 RightArrow처럼 받지 않고 지금처럼 그 자리를 자동완성한다.
    output, lines = _type_in_a_tab(tmp_path, "Write-Output hq-t", [LEFT, LEFT, LEFT, TAB])
    assert _SUGGESTION_SHOWN.search(output), repr(output)
    assert r".\hq-tab-completed.txt" in lines, lines
    assert "hq-tab-accepted" not in lines, lines


def test_tab_completes_as_before_when_no_suggestion_is_shown(tmp_path, needs_accept_suggestion):
    # 기록에 'Write-Output $hqv'로 시작하는 줄이 없다
    _, lines = _type_in_a_tab(tmp_path, "Write-Output $hqv", [TAB])
    assert "tab-completed" in lines, lines


def test_repeated_tab_still_cycles_through_completions(tmp_path, needs_accept_suggestion):
    _, lines = _type_in_a_tab(tmp_path, "Write-Output $hqv", [TAB, TAB])
    assert "tab-cycled" in lines, lines


def test_ctrl_space_still_completes_while_a_suggestion_is_shown(tmp_path, needs_accept_suggestion):
    # Ctrl+Space(MenuComplete)는 후보가 하나면 바로 채운다. 'hq-t'의 후보는 작업 폴더의 hq-tab-completed.txt다.
    output, lines = _type_in_a_tab(tmp_path, "Write-Output hq-t", [CTRL_SPACE])
    assert _SUGGESTION_SHOWN.search(output), repr(output)
    assert r".\hq-tab-completed.txt" in lines, lines
    assert "hq-tab-accepted" not in lines, lines


def test_control_without_the_binding_tab_does_not_take_the_suggestion(tmp_path, needs_accept_suggestion):
    # 대조군: 같은 도구, 같은 입력이고 미리보기도 보였다. 키 설정이 없으면 Tab은 미리보기를 받지 않는다.
    # 그래서 위 시험의 hq-tab-accepted는 키 설정 덕분이다.
    output, lines = _type_in_a_tab(tmp_path, "Write-Output hq-t", [TAB], binding=False)
    assert _SUGGESTION_SHOWN.search(output), repr(output)
    assert "hq-tab-accepted" not in lines, lines


# 규칙 R16b-2: 자동완성 순환이 시작되면 Tab은 미리보기를 받지 않고 다음 후보로 간다.
# 'hq-tab-a'의 후보는 두 파일이고, 기록 한 줄이 첫 후보로 시작한다. 그래서 첫 Tab이 .\hq-tab-a1.txt를 채우면
# PSReadLine이 그 뒤에 회색 미리보기(-from-history)를 그린다.
CYCLE_FILES = ("hq-tab-a1.txt", "hq-tab-a2.txt")
CYCLE_HISTORY = r"Write-Output .\hq-tab-a1.txt-from-history"


def _suggestion_shown(output: str, typed: str, gray: str) -> bool:
    """typed 바로 뒤에 색을 바꾸는 시퀀스가 오고 회색 미리보기 gray가 이어져 그려졌는가."""
    return re.search(re.escape(typed) + r"(?:\x1b\[[0-9;]*m)+" + re.escape(gray), output) is not None


def test_tab_keeps_cycling_once_a_completion_cycle_started(tmp_path, needs_accept_suggestion):
    output, lines = _type_in_a_tab(
        tmp_path, "Write-Output hq-tab-a", [TAB, TAB], history=CYCLE_HISTORY, files=CYCLE_FILES
    )
    assert _suggestion_shown(output, r".\hq-tab-a1.txt", "-from-history"), repr(output)  # 첫 Tab 뒤에 보였다
    assert r".\hq-tab-a2.txt" in lines, lines
    assert r".\hq-tab-a1.txt-from-history" not in lines, lines


def test_control_the_first_tab_still_accepts_a_shown_suggestion(tmp_path, needs_accept_suggestion):
    # 대조군: 같은 기록과 같은 후보. 친 글자에 처음부터 미리보기가 보이면 첫 Tab은 그것을 받는다.
    output, lines = _type_in_a_tab(
        tmp_path, r"Write-Output .\hq-tab-a1", [TAB], history=CYCLE_HISTORY, files=CYCLE_FILES
    )
    assert _suggestion_shown(output, r".\hq-tab-a1", ".txt-from-history"), repr(output)
    assert r".\hq-tab-a1.txt-from-history" in lines, lines


def test_any_other_edit_ends_the_completion_cycle(tmp_path, needs_accept_suggestion):
    # Tab으로 .\hq-tab-a1.txt를 채운 뒤 글자를 하나 치면 순환이 끝난다. 다음 Tab은 다시 미리보기를 받는다.
    _, lines = _type_in_a_tab(
        tmp_path, "Write-Output hq-tab-a", [TAB, "-", TAB], history=CYCLE_HISTORY, files=CYCLE_FILES
    )
    assert r".\hq-tab-a1.txt-from-history" in lines, lines


_KEYS_REPORT = (
    "foreach ($k in 'Tab', 'Shift+Tab', 'RightArrow', 'F2', 'Ctrl+Spacebar') {"
    ' Write-Output ("$k=" + (Get-PSReadLineKeyHandler -Chord $k).Function) }\n'
)


def test_tab_binding_changes_tab_and_nothing_else(needs_accept_suggestion):
    script = (
        "Import-Module PSReadLine\n"
        + _KEYS_REPORT
        + "$ErrorActionPreference = 'Stop'\n"
        + bootstrap.tab_binding()
        + _KEYS_REPORT
    )
    result = run_ps(script)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    before, after = lines[:5], lines[5:]
    assert before[0] == "Tab=TabCompleteNext", lines
    assert after[0] == "Tab=AcceptSuggestionOrTabComplete", lines
    assert after[1:] == before[1:], lines


def test_tab_binding_is_silent_when_psreadline_is_not_loaded():
    # -NonInteractive에서는 PSReadLine을 불러오지 않는다. 그래도 오류 없이 지나가고 아무것도 찍지 않는다.
    loaded = "Write-Output ([bool]('Microsoft.PowerShell.PSConsoleReadLine' -as [type]))\n"
    script = loaded + "$ErrorActionPreference = 'Stop'\n" + bootstrap.tab_binding() + loaded
    result = run_ps(script)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["False", "False"], result.stdout
    assert 'S="Error"' not in result.stderr, result.stderr


def test_tab_binding_leaves_psreadline_2_0_alone():
    # Windows에 들어 있는 PSReadLine 2.0.0에는 AcceptSuggestion이 없다. Tab은 그대로 두고 아무것도 찍지 않는다.
    script = (
        "if (-not (Get-Module PSReadLine -ListAvailable | Where-Object { $_.Version -eq [version]'2.0.0' })) {"
        " Write-Output 'no-2.0.0'; exit }\n"
        "Import-Module PSReadLine -RequiredVersion 2.0.0\n"
        "$ErrorActionPreference = 'Stop'\n"
        + bootstrap.tab_binding()
        + "Write-Output ((Get-PSReadLineKeyHandler | Where-Object { $_.Key -eq 'Tab' }).Function)\n"
    )
    result = run_ps(script)
    if result.stdout.strip() == "no-2.0.0":
        pytest.skip("이 PC에 PSReadLine 2.0.0이 없다")
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["TabCompleteNext"], result.stdout
    assert 'S="Error"' not in result.stderr, result.stderr


def test_a_failed_tab_binding_prints_one_line_and_the_script_goes_on(needs_accept_suggestion):
    # Set-PSReadLineKeyHandler를 실패하는 같은 이름의 함수로 바꿔 둔다(PowerShell은 cmdlet보다 함수를 먼저 찾는다).
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        + _tab_setup()
        + "function Set-PSReadLineKeyHandler { throw 'hq-test-failure' }\n"
        + bootstrap.tab_binding()
        + "Write-Output ('next' + '-statement')\n"  # 부트스트랩에서는 이 자리에 Enter-PSSession이 온다
    )
    output, code = run_pty(encoded_argv(script), writes=[(2.0, "exit\r")])
    assert code == 0, output
    lines = _printed_lines(output)
    message = "Tab으로 미리보기 받기를 켜지 못했습니다: hq-test-failure"
    assert lines.count(message) == 1, lines
    assert lines.index(message) < lines.index("next-statement"), lines
