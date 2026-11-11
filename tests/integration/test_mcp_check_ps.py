"""hq.mcp.querycheck를 이 PC의 실제 powershell.exe로 확인한다. 검사만 하고 아무것도 실행하지 않는다.

여기의 스크립트는 검사기에 자료로만 넘긴다. 공격 표의 스크립트는 어떤 경우에도 실행하지 않는다.
"""

from pathlib import Path

import pytest
from mcp_cases import NO_PARSE, PASS_SCRIPTS, spec_attacks
from ps import parse_errors

from hq.mcp.querycheck import check_query

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def codes(script):
    verdict = check_query(script)
    return verdict.ok, {r.split(":", 1)[0] for r in verdict.reasons}


def codes_by_line(script):
    """검사 하나로 여러 줄을 본다. 이유마다 끝의 "(줄 N)"을 읽어 {줄: {코드}}로 모은다."""
    verdict = check_query(script)
    found = {}
    for reason in verdict.reasons:
        line = int(reason.rsplit("(줄 ", 1)[1].removesuffix(")"))
        found.setdefault(line, set()).add(reason.split(":", 1)[0])
    return verdict.ok, found


# 검사 하나가 powershell.exe를 하나 띄운다(이 PC에서 약 2.7초). 58줄을 차례로 검사하면 기본 한도 120초를 넘는다
@pytest.mark.timeout(600)
def test_every_attack_in_the_spec_is_refused():
    attacks = spec_attacks()
    assert len(attacks) >= 58 and "Get-Process | Stop-Process" in attacks
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
    "Get-SmbConnection | Get-CimInstance Win32_OperatingSystem": "PIPELINE_INPUT",
    "Get-CimInstance CIM_Product": "WMI_CLASS",
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


# 보이지 않는 글자는 시험 글에 이스케이프로만 쓴다. U+00AD 소프트 하이픈, U+200D 폭 없는 이음, U+00DF ß
@pytest.mark.parametrize(
    "script",
    [
        "Get-Service -Comp\u00aduterName 192.0.2.10",
        "Get-NetAdapter -CimSe\u00dfion 192.0.2.10",
        "Get-Date -Out\u00adVariable PSDefaultParameterValues",
        "Get-Service -Comp\u200duterName 192.0.2.10",
        "Get-NetAdapter -CimSe\u200dssion 192.0.2.10",
        "Get-Date -Out\u200dVariable PSDefaultParameterValues",
    ],
)
def test_a_parameter_name_with_any_character_but_letters_digits_and_underscore_is_refused(script):
    # 바인더는 매개변수 이름을 언어 규칙으로 비교해 이런 글자를 무시하거나 ß를 ss로 본다(2026-11-11 측정)
    ok, found = codes(script)
    assert not ok and "PARAM_NAME" in found


@pytest.mark.parametrize(
    "script, expected",
    [
        # 줄 1은 이름이 nu<소프트 하이픈>ll인 평범한 변수에 경로를 넣고, 줄 2는 그 경로로 쓴다. $null이 아니다
        ("Write-Output C:\\Users\\Public\\hq-x.txt -OutVariable 'nu\u00adll'\nGet-Date > ${nu\u00adll}", {"REDIRECT"}),
        ("Get-Date > ${nul}", {"REDIRECT"}),
        ("Get-CimInstance Win32_Pro\u00adduct", {"WMI_CLASS"}),
        ("Get-Item fun\u00adction:\\x", {"PATH"}),
        ("Get-Content \\\u00ad\\z\\c$\\x", {"PATH"}),
    ],
)
def test_names_with_an_invisible_character_are_compared_as_text_not_by_language(script, expected):
    ok, found = codes(script)
    assert not ok and expected <= found


def test_a_command_whose_remote_parameter_binds_from_the_pipeline_may_only_start_it():
    ok, found = codes("'spooler' | Get-Service")
    assert not ok and "PIPELINE_INPUT" in found


@pytest.mark.parametrize(
    "script",
    [
        "Get-Service -Name spooler",
        "Get-CimInstance Win32_OperatingSystem | Select-Object Caption",
        "Get-Service | Where-Object Status -eq Running | Select-Object Name",
    ],
)
def test_ordinary_names_and_pipelines_that_start_with_such_a_command_pass(script):
    assert codes(script) == (True, set())


@pytest.mark.parametrize("name", list(PASS_SCRIPTS))
def test_the_shapes_claude_usually_writes_pass(name):
    assert codes(PASS_SCRIPTS[name]) == (True, set()), name


EXPECTED_9 = {
    "[IO.File]::Delete('x')": "TYPE",
    "[scriptblock]::Create('Remove-Item x').Invoke()": "TYPE",
    "$t = [int]; $t::Parse('1')": "TYPE",
    "(Get-Item x).Delete()": "METHOD",
    "$f = Get-Item x; $f.Delete()": "METHOD",
    "(Get-Item x).Format('NTFS')": "METHOD",
    "(Get-Item x).Replace('y','z')": "METHOD",
    "$f = Get-Item x; $f.Replace('y','z')": "METHOD",
    "[int].Assembly.GetType('System.IO.File')": "METHOD",
    "$ExecutionContext.InvokeCommand.InvokeScript('Remove-Item x')": "VARIABLE",
    "$env:Path = 'x'": "ASSIGN",
    "$f = Get-Item x; $f.Attributes++": "ASSIGN",
    "$f = Get-Item x; --$f.Attributes": "ASSIGN",
    "$a, $f.IsReadOnly = 1, $true": "ASSIGN",
    "$psdefaultparametervalues = @{}": "ASSIGN",
    "$PSDefaultParameterValues = @{'*:ComputerName'='z'}; Get-Service": "ASSIGN",
    "foreach ($PSDefaultParameterValues in @(@{'*:ComputerName'='z'})) { Get-Service }": "ASSIGN",
    "'C:\\x' -as 'System.IO.StreamWriter'": "AS",
    "$t = 'System.IO.StreamWriter'; 'C:\\x' -as $t": "AS",
    "'C:\\x' -as ([int].Assembly.DefinedTypes | Where-Object { $_.Name -eq 'StreamWriter' })": "AS",
    "switch -File \\\\z\\c$\\x { default { $_ } }": "SWITCH_FILE",
    "$using:cred.UserName": "USING",
    '"$(Remove-Item x)"': "COMMAND",
    "Get-Process | Where-Object { Remove-Item x }": "COMMAND",
}


@pytest.mark.parametrize("script, code", EXPECTED_9.items())
def test_each_refusal_of_an_expression_names_its_reason(script, code):
    assert script in spec_attacks()
    ok, found = codes(script)
    assert not ok and code in found


@pytest.mark.parametrize(
    "script, code",
    [
        ("$global:x = 1", "ASSIGN"),
        ("${C:\\x} = 1", "ASSIGN"),
        ("$h = @{}; $h['a'] = 1", "ASSIGN"),
        ("$h = @{}; $h.a = 1", "ASSIGN"),
        ("$function:prompt", "VARIABLE"),
        ("${C:\\x}", "VARIABLE"),
        ("$Host.UI", "VARIABLE"),
        ("[System.IO.FileInfo]'x'", "TYPE"),
        ("[System.Collections.Generic.List[int]]@()", "TYPE"),
        ("'a'.GetType()", "METHOD"),
        ("$n = 'Delete'; (Get-Item x).$n()", "METHOD"),
        ("[math]::Pow(2, 3)", "METHOD"),
        ("$t = [int]; $t::MaxValue", "TYPE"),
        ("Get-Content x 3>&1", "REDIRECT"),
        ("Get-Content x 2> y.txt", "REDIRECT"),
        ("param($x) $x", "DEFINITION"),
        ("trap { } Get-Date", "DEFINITION"),
        ("class C { }", "DEFINITION"),
        ("Get-Date -ov +Matches", "PARAM_VARIABLE"),
    ],
)
def test_other_forms_the_spec_names_are_refused(script, code):
    ok, found = codes(script)
    assert not ok and code in found


# 변수, 멤버, 형식 이름에 출력 가능한 ASCII 밖의 글자가 있으면 목록과 견주기 전에 거절한다. 맨 이름
# ($a<U+00AD>b, 'x'.Fo<U+00AD>rmat)은 파서가 보이지 않는 글자에서 토큰을 끊어 문법 오류가 되므로, 이름이 하나로 남는
# 중괄호와 따옴표 모양으로 본다. U+00AD 소프트 하이픈, U+0131 점 없는 i
@pytest.mark.parametrize(
    "script, code",
    [
        ("${PSDefault\u00adParameterValues} = @{}", "VARIABLE"),
        ("${e\u00adnv:Path}", "VARIABLE"),
        ("'x'.'For\u00admat'('{0}')", "METHOD"),
        ("[string]::'For\u00admat'('{0}', 1)", "METHOD"),
        ("'x'.'Le\u00adngth'", "METHOD"),
        ("[\u0131nt]::MaxValue", "TYPE"),
        ("'5' -as [\u0131nt]", "TYPE"),
    ],
)
def test_a_name_with_any_character_outside_printable_ascii_is_refused(script, code):
    ok, found = codes(script)
    assert not ok and code in found


# 스크립트 블록 안의 문자열은 어디에 있든 경로 규칙을 따른다. 블록의 결과는 지연 바인딩(매개변수 값으로 준 블록을
# 파이프라인 입력마다 실행), 파이프라인, 계산 속성으로 다른 명령의 경로 매개변수에 닿는다. 괄호나 $( )로 감싼 블록과
# 변수에 담은 블록도 지연 바인딩된다(2026-11-11 이 PC에서 Get-Date -Date로 측정)
@pytest.mark.parametrize(
    "script, expected",
    [
        ("'x' | Get-Content -Path { '\\\\z\\c$\\x' }", {"SCRIPT_BLOCK_ARG", "PATH"}),
        ("'x' | Get-Content -Path:{ 'C:\\x' }", {"SCRIPT_BLOCK_ARG"}),
        ("'x' | Get-Content ({ 'C:\\x' })", {"SCRIPT_BLOCK_ARG"}),
        ("'x' | Get-Content $({ 'C:\\x' })", {"SCRIPT_BLOCK_ARG"}),
        ("$b = { '\\\\z\\c$\\x' }; 'x' | Get-Content -Path $b", {"PATH"}),
        ("1 | ForEach-Object { 'FileSystem::C:\\x' } | Get-Content", {"PATH"}),
        ("1 | Select-Object @{n='Path';e={'\\\\z\\c$\\x'}} | Get-Content", {"PATH"}),
        ("$b = { 'fun\u00adction:x' }", {"PATH"}),
    ],
)
def test_no_string_in_a_script_block_gets_past_the_path_rule(script, expected):
    ok, found = codes(script)
    assert not ok and expected <= found


def test_every_opened_construct_keeps_the_pipeline_and_parameter_name_rules():
    # 줄마다 허용 목록의 명령이 괄호, 하위 식, 스크립트 블록, 제어 흐름 안에 있다. 명령은 자기 파이프라인으로 판단한다
    lines = [
        ("(1 | Get-Service)", "PIPELINE_INPUT"),
        ('"$(1 | Get-Service)"', "PIPELINE_INPUT"),
        ("Get-Date | ForEach-Object { $_ | Get-Service }", "PIPELINE_INPUT"),
        ("if ($true) { 'x' | Get-Process }", "PIPELINE_INPUT"),
        ("foreach ($s in 1) { $s | Get-HotFix }", "PIPELINE_INPUT"),
        ("try { 'x' | Get-CimInstance Win32_OperatingSystem } catch { }", "PIPELINE_INPUT"),
        ("$b = { 'x' | Get-NetIPConfiguration }", "PIPELINE_INPUT"),
        ("(Get-Service -Comp\u00aduterName z)", "PARAM_NAME"),
        ("1 | ForEach-Object { Get-Date -Out\u00adVariable x }", "PARAM_NAME"),
        ("while ($false) { Get-Service -Comp\u00aduterName z }", "PARAM_NAME"),
        ("switch (1) { 1 { Get-Service -ComputerName z } }", "PARAM_REMOTE"),
    ]
    ok, found = codes_by_line("\n".join(script for script, _ in lines))
    assert not ok
    assert {n: code in found.get(n, set()) for n, (_, code) in enumerate(lines, 1)} == dict.fromkeys(
        range(1, len(lines) + 1), True
    )


def test_other_ways_around_the_new_rules_are_refused():
    # 범위 표시 variable:은 파서가 드라이브로 보지 않는다($variable:x는 $x다). 그래도 넣을 수 있는 자리가 아니다
    lines = [
        ("$variable:PSDefaultParameterValues = @{}", "ASSIGN"),
        ("$variable:ExecutionContext", "VARIABLE"),
        ("$script:Host", "VARIABLE"),
        ("[int, mscorlib]'1'", "TYPE"),
        ("[string[]]::Join(',', 'a')", "METHOD"),
        ("@(1).ForEach({ 1 })", "METHOD"),
        ('$x."$n"()', "METHOD"),
        ("$a = 1, 2; ++$a[0]", "ASSIGN"),
        ("[IO.FileInfo]$x = 'x'", "ASSIGN"),
        ("$x = 1; [int][string]$x = 1", "ASSIGN"),
        ("$b = { param($x) $x }", "DEFINITION"),
        ("$b = { Remove-Item x }", "COMMAND"),
        ("1 | Get-Date -Date { 1 }", "SCRIPT_BLOCK_ARG"),
    ]
    ok, found = codes_by_line("\n".join(script for script, _ in lines))
    assert not ok
    assert {n: code in found.get(n, set()) for n, (_, code) in enumerate(lines, 1)} == dict.fromkeys(
        range(1, len(lines) + 1), True
    )


def test_other_shapes_of_the_opened_kinds_pass():
    # 검사 하나로 본다. 걸리면 이유의 줄 번호가 어느 줄인지 알려 준다
    script = "\n".join(
        [
            "$x = $env:COMPUTERNAME; $y = $global:x; ${z} = $x[0]; $w = @{ a = 1 }; $w['a']",
            '(Get-Service -Name x).Status; "$(Get-Process -Id 1)"; Get-Date | ForEach-Object { Get-Service -Name x }',
            "Get-Process | Sort-Object { $_.Id } | Group-Object { $_.Name } | Format-Table Name, { $_.Count }",
            "[datetime]::Now; [math]::PI; [int]::MaxValue; [string[]]$s = 'a', 'b'; $s -is [array]",
            "for ($i = 0; $i -lt 2; $i++) { if ($i) { continue } else { break } }; while ($false) { }",
            "do { $i-- } while ($i -gt 0); do { } until ($true)",
            "try { throw 'x' } catch [System.IO.IOException] { $_ } finally { }",
            "switch -Wildcard ('ab') { 'a*' { 'yes' } { $_ -eq 'b' } { 'no' } }",
            "$t = 'a b'.Split(' ').Trim().Substring(0).ToLower(); [regex]::Replace($t, 'a', 'b'); exit 0",
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
