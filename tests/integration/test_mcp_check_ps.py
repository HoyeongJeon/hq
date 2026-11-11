"""hq.mcp.querycheck를 이 PC의 실제 powershell.exe로 확인한다. 검사만 하고 아무것도 실행하지 않는다.

여기의 스크립트는 검사기에 자료로만 넘긴다. 공격 표의 스크립트는 어떤 경우에도 실행하지 않는다.
"""

from pathlib import Path

import pytest
from mcp_cases import NO_PARSE, spec_attacks
from ps import parse_errors

from hq.mcp.querycheck import check_query

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def codes(script):
    verdict = check_query(script)
    return verdict.ok, {r.split(":", 1)[0] for r in verdict.reasons}


# 검사 하나가 powershell.exe를 하나 띄운다(이 PC에서 약 2.7초). 56줄을 차례로 검사하면 기본 한도 120초를 넘는다
@pytest.mark.timeout(600)
def test_every_attack_in_the_spec_is_refused():
    attacks = spec_attacks()
    assert len(attacks) >= 56 and "Get-Process | Stop-Process" in attacks
    for script in attacks:
        verdict = check_query(script)
        assert verdict.ok is False, script
        # 검사기가 깨져서 모두 거절되는 것과 구별한다: 규칙에 걸려 거절되어야 한다
        assert not any(r.startswith("CHECK_FAILED") for r in verdict.reasons), (script, verdict.reasons)


@pytest.mark.parametrize("script", NO_PARSE)
def test_the_no_parse_table_never_reaches_the_parser(script):
    verdict = check_query(script, run=lambda *a, **k: pytest.fail("파서를 불렀다"))
    assert verdict.ok is False and verdict.reasons[0].startswith("PARSER_HAZARD")


EXPECTED_8 = {
    "Get-Process | Stop-Process": "COMMAND",
    "iex 'Remove-Item x'": "COMMAND",
    "Get-WmiObject Win32_Process | % { $_.Terminate() }": "COMMAND",
    "Get-Command C:\\x.ps1 | Select-Object -ExpandProperty ScriptBlock": "COMMAND",
    "& ('Rem'+'ove-Item') x": "INVOKE",
    ". { Remove-Item x }": "INVOKE",
    "gci | % Delete": "FOREACH_FORM",
    "gci | ForEach-Object -MemberName Delete": "FOREACH_FORM",
    "1 | ForEach-Object -Process (Get-Command C:\\x.ps1).ScriptBlock": "FOREACH_FORM",
    "Get-Content x > y": "REDIRECT",
    "ipconfig /release": "EXTERNAL_ARGS",
    "Get-Service -ComputerName z": "PARAM_REMOTE",
    "Get-Service -Comp z": "PARAM_REMOTE",
    "Get-ChildItem -PSSession $s": "PARAM_REMOTE",
    "Get-CimInstance Win32_OperatingSystem -ServerName z": "PARAM_REMOTE",
    "Get-CimInstance Win32_OperatingSystem -Server z": "PARAM_REMOTE",
    "Get-HotFix -IPAddress z": "PARAM_REMOTE",
    "Get-NetIPConfiguration -PSComputerName z": "PARAM_REMOTE",
    "Get-Date -OutVariable PSDefaultParameterValues": "PARAM_VARIABLE",
    "$p = @{ComputerName='z'}; Get-Service @p": "SPLAT",
    "function Get-Process { Remove-Item x }; Get-Process": "DEFINITION",
    "Get-Content \\\\z\\c$\\x": "PATH",
    "Get-Content FileSystem::\\\\z\\c$\\x": "PATH",
    "Get-Content -Path:\\\\z\\c$\\x": "PATH",
    "Get-Content FileSystem::C:\\x": "PATH",
    "Get-Item function:prompt": "PATH",
    'Get-Content "\\\\$h\\c$\\x"': "PATH",
    "Get-CimInstance Win32_Product": "WMI_CLASS",
    "Get-CimInstance -Query \"SELECT * FROM Win32_PingStatus WHERE Address='z'\"": "WMI_CLASS",
}


@pytest.mark.parametrize("script, code", EXPECTED_8.items())
def test_each_refusal_names_its_reason(script, code):
    assert script in spec_attacks()
    ok, found = codes(script)
    assert not ok and code in found


@pytest.mark.parametrize(
    "script, code",
    [
        # ArgumentList의 별칭. 넘기면 ForEach-Object가 MemberName 형태가 되어 스크립트 블록의 글자를 메서드 이름으로
        # 쓴다(2026-11-11 이 PC에서 'abc' | % -Args {} {ToUpper}로 확인)
        ("gci | % -Args {} {ToUpper}", "FOREACH_FORM"),
        ("1 | ForEach-Object -Process:(Get-Date)", "FOREACH_FORM"),  # 붙은 값도 스크립트 블록이어야 한다
        ("Get-Content \\/z\\c$\\x", "PATH"),  # PowerShell은 /도 경로 구분자로 받는다
        ("Get-Content -Path:function:prompt", "PATH"),
        ("Get-Date -ov global:x", "PARAM_VARIABLE"),
        ("Get-Date -OutVariable", "PARAM_VARIABLE"),
        ("Get-Process -C z", "PARAM_REMOTE"),
        ("netstat -an 5", "EXTERNAL_ARGS"),
        ("whoami /upn", "EXTERNAL_ARGS"),
        ("Get-Content x >> y", "REDIRECT"),
        ("Get-Process *>&1", "REDIRECT"),
        ("begin { } process { Get-Date }", "DEFINITION"),
        ("#requires -Version 5\nGet-Date", "DEFINITION"),
        ("[ValidateNotNull()]$x = 1", "NODE"),
    ],
)
def test_other_forms_of_these_rules_are_refused(script, code):
    ok, found = codes(script)
    assert not ok and code in found


@pytest.mark.parametrize(
    "script",
    [
        "Get-Service",
        "Get-Service | Sort-Object Name | Format-Table -AutoSize",
        "gci C:\\Windows -Recurse -File -ErrorAction SilentlyContinue",
        "netstat -ano",
        "netstat -a -n",
        "whoami /all",
        "hostname",
        "tasklist /v | Select-String svchost",
        "Get-ItemProperty 'Registry::HKEY_USERS\\*\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run' 2>$null",
        "Get-NetIPAddress -IPAddress 10.0.1.21",
        "Get-SmbConnection -ServerName fs01",
        "Get-Date -OutVariable now | Out-Null",
        "Get-Process 2>&1",
        "Get-Service\r\nGet-Process",
    ],
)
def test_plain_commands_pass(script):
    assert codes(script) == (True, set())


def test_other_plain_forms_pass():
    # 검사 하나로 본다(검사마다 powershell.exe를 띄운다). 걸리면 이유의 줄 번호가 어느 줄인지 알려 준다
    script = "\n".join(
        [
            "netstat -a -n -o -e -r -s",
            "ipconfig /all",
            "systeminfo",
            "quser",
            "tasklist",
            "Get-Date -OutVariable +now",
            "Get-ChildItem -Path:C:\\Windows -Filter *.log >$null",
        ]
    )
    verdict = check_query(script)
    assert (verdict.ok, verdict.reasons) == (True, ())


def test_a_syntax_error_is_a_refusal():
    assert codes("Get-Service |")[1] == {"PARSE_ERROR"}


def test_the_check_template_parses_and_never_runs_the_script(tmp_path):
    text = (ROOT / "src/hq/ps/mcp-check.ps1").read_text(encoding="utf-8")
    assert parse_errors(text, tmp_path) == []
    for word in ("Invoke-Expression", "iex ", ".Invoke(", "GetScriptBlock", "SafeGetValue", "Invoke-Command"):
        assert word not in text, word
    marker = tmp_path / "ran.txt"
    check_query(f"Set-Content -LiteralPath '{marker}' -Value x")
    assert not marker.exists()
