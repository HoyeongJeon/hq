import re

import pytest
from ps import parse_errors, run_ps
from ptyrun import run_pty

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
NASTY = "웹01'; Remove-Item C:\\ -Recurse; \"$x`;\n\u2018\u2019\u201a\u201b 🙂"
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
