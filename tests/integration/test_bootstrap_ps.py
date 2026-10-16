import pytest
from ps import parse_errors, run_ps
from ptyrun import run_pty

from hq.bootstrap import BootstrapParams, build_command, build_script, encoded_argv, prelude, ps_json_literal

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


# (FullyQualifiedErrorId, 메시지, 기대 종료 코드). Task 6에서 실서버 표본(V3)을 더한다.
CASES = [
    ("AccessDenied,PSSessionOpenFailed", "액세스가 거부되었습니다.", 10),
    ("ServerNotTrusted,PSSessionOpenFailed", "The WinRM client cannot process the request. TrustedHosts", 12),
    ("-2144108060,PSSessionOpenFailed", "This user is allowed a maximum number of 5 concurrent shells", 13),
    ("CannotConnect,PSSessionOpenFailed", "WinRM cannot complete the operation.", 11),
    ("WinRMOperationTimeout,PSSessionOpenFailed", "The operation has timed out.", 11),
    ("ComputerNotFound,PSSessionOpenFailed", "The network path was not found.", 11),
    ("SomethingElse,PSSessionOpenFailed", "알 수 없음", 1),
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
