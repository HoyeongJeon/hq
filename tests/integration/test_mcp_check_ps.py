"""hq.mcp.querycheck를 이 PC의 실제 powershell.exe로 확인한다. 검사만 하고 아무것도 실행하지 않는다.

여기의 스크립트는 검사기에 자료로만 넘긴다. 공격 표의 스크립트는 어떤 경우에도 실행하지 않는다.
"""

from pathlib import Path

import pytest
from mcp_cases import NO_PARSE, PASS_SCRIPTS, spec_attacks
from ps import parse_errors, run_ps

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


# 검사 하나가 powershell.exe를 하나 띄운다(이 PC에서 약 2.7초). 69줄을 차례로 검사하면 기본 한도 120초를 넘는다
@pytest.mark.timeout(600)
def test_every_attack_in_the_spec_is_refused():
    attacks = spec_attacks()
    assert len(attacks) >= 69 and "Get-Process | Stop-Process" in attacks
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


def assert_each_line_refused(lines):
    """줄마다 (스크립트, 코드). 검사 하나로 모두 보고, 줄마다 그 줄 번호의 이유에 코드가 있는지 본다."""
    ok, found = codes_by_line("\n".join(script for script, _ in lines))
    assert not ok
    missing = {n: (script, code) for n, (script, code) in enumerate(lines, 1) if code not in found.get(n, set())}
    assert missing == {}


def test_strings_in_a_shaping_command_block_are_code_not_arguments():
    # 경로 규칙은 글자로 적은 명령 인자만 본다(스펙 7.2). 다듬기 명령에 준 블록 안의 문자열은 인자가 아니다
    script = "\n".join(
        [
            "Get-NetTCPConnection | Where-Object { $_.LocalAddress -ne '::1' }",
            'Get-Volume | ForEach-Object { "$([math]::Round($_.Size/1GB,1)) GB" }',
            "Get-ChildItem | ForEach-Object { ($_.FullName -split '\\\\')[-1] }",
            "Get-ChildItem | Where-Object { $_.Path -match '\\\\AppData\\\\' }",
            "Get-ChildItem C:\\ | Select-Object @{n='Leaf';e={($_.FullName -split '\\\\')[-1]}}",
        ]
    )
    verdict = check_query(script)
    assert (verdict.ok, verdict.reasons) == (True, ())


@pytest.mark.parametrize(
    "script",
    [
        "Get-ChildItem | Where-Object { Test-Path '\\\\z\\c$\\x' }",
        'Get-ChildItem | ForEach-Object { Get-Content "\\\\$($_.Name)\\c$\\x" }',
        "Get-Content (1 | ForEach-Object { '\\\\z\\c$\\x' })",
        "Get-Content -Path:$('\\\\z\\c$\\x')",
        "Get-Item ('\\\\z\\c$\\x')",
        "Get-Item @('\\\\z\\c$\\x')",
    ],
)
def test_a_path_written_in_a_command_argument_is_refused_wherever_the_command_is(script):
    ok, found = codes(script)
    assert not ok and "PATH" in found


def test_paths_that_reach_a_command_another_way_pass_as_the_limits_spec_7_3_states():
    # 회귀가 아니다. 스펙 7.3이 적은 한계: 변수, 파이프라인, 개체 속성, 블록의 결과로 온 문자열은 인자가 아니다
    script = "\n".join(
        [
            "$p = '\\\\z\\c$\\x'; Get-Content $p",
            "'\\\\z\\c$\\x' | Get-Item",
            "[pscustomobject]@{Path='\\\\z\\c$\\x'} | Get-Content",
            "1 | ForEach-Object { '\\\\z\\c$\\x' } | Get-Content",
        ]
    )
    verdict = check_query(script)
    assert (verdict.ok, verdict.reasons) == (True, ())


def test_every_argument_of_get_ciminstance_must_be_written_literally():
    # 클래스 이름을 이어 붙여 만들면 이름 검사를 지나친다
    assert_each_line_refused(
        [
            ("Get-CimInstance -ClassName ('Win32_Ping' + 'Status') -Filter \"Address='192.0.2.10'\"", "WMI_CLASS"),
            ("Get-CimInstance ('Win32_Pro' + 'duct')", "WMI_CLASS"),
            ("$c = 'Win32_Pro'; Get-CimInstance \"${c}duct\"", "WMI_CLASS"),
            ("Get-CimInstance ('{0}_Product' -f 'Win32')", "WMI_CLASS"),
            ("Get-CimInstance ('Win32_PingXStatus' -replace 'X', '') -Filter \"Address='192.0.2.10'\"", "WMI_CLASS"),
            ("Get-CimInstance -Query ('SELECT * FROM Win32_Pro' + 'duct')", "WMI_CLASS"),
            ("Get-CimInstance (('Win32', 'Product') -join '_')", "WMI_CLASS"),
            ("Get-CimInstance \"Win32_Pro$('duct')\"", "WMI_CLASS"),
        ]
    )


def test_a_variable_in_a_filter_or_index_of_on_a_string_is_refused_as_the_price_of_the_rulings():
    assert_each_line_refused(
        [
            ('Get-CimInstance Win32_Process -Filter "ProcessId=$p"', "WMI_CLASS"),
            ("'abc'.IndexOf('b')", "METHOD"),
        ]
    )


def test_the_input_object_of_get_service_and_get_process_is_a_remote_parameter():
    # 해시테이블이 MachineName을 가진 ServiceController로 바뀌어 묶인다
    table = "@{ MachineName = '192.0.2.10'; ServiceName = 'spooler' }"
    assert_each_line_refused(
        [
            (f"Get-Service -InputObject {table} | Select-Object Status", "PARAM_REMOTE"),
            (f"Get-Service -InputObject:{table} | Select-Object Status", "PARAM_REMOTE"),
            (f"$s = {table}; Get-Service -InputObject $s | Select-Object Status", "PARAM_REMOTE"),
            (f"Get-Service -InputObject ([pscustomobject]{table}) | Select-Object Status", "PARAM_REMOTE"),
            ("Get-Service -Inp $s", "PARAM_REMOTE"),
            ("Get-Process -InputObject $s", "PARAM_REMOTE"),
        ]
    )


def test_the_logging_preferences_and_transcript_are_not_assignable():
    # 새 세션에 없어 변수 목록에 잡히지 않는다. 서버에서 뒤의 모든 명령이 이벤트 로그에 기록을 남기게 된다
    names = [
        "LogCommandLifecycleEvent",
        "LogCommandHealthEvent",
        "LogEngineLifecycleEvent",
        "LogEngineHealthEvent",
        "LogProviderLifecycleEvent",
        "LogProviderHealthEvent",
    ]
    assert_each_line_refused(
        [(f"${name} = $true; Get-Date", "ASSIGN") for name in names]
        + [
            ("Get-Date -OutVariable LogCommandLifecycleEvent", "PARAM_VARIABLE"),
            ("foreach ($LogEngineHealthEvent in 1) { }", "ASSIGN"),
            ("$Transcript = 'x'", "ASSIGN"),
        ]
    )


def test_a_hashtable_key_is_plain_text_and_never_pstypename():
    # PSTypeName은 개체에 다른 타입의 이름을 붙여, 속성을 읽을 때 그 타입에 딸린 코드가 돌게 한다
    assert_each_line_refused(
        [
            ("[pscustomobject]@{ PSTypeName = 'System.IO.FileInfo'; FullName = 'C:\\x' }", "TYPE"),
            ("[pscustomobject]@{ ('PSType' + 'Name') = 'System.IO.FileInfo'; FullName = 'C:\\x' }", "HASH_KEY"),
            (
                "$o = [pscustomobject]@{ PSTypeName = 'System.IO.FileInfo'; FullName = '\\\\z\\c$\\x.dll' }; "
                "$o.VersionInfo",
                "TYPE",
            ),
            (
                "[pscustomobject]@{ PSTypeName = 'System.Management.ManagementObject#root\\cimv2\\Win32_Ping' + "
                "'Status'; Address = 'x.example' } | Select-Object IPV4Address",
                "TYPE",
            ),
            ("[ordered]@{ pstypename = 'x' }", "TYPE"),
            ("@{ 'PSType\u00adName' = 'x' }", "TYPE"),
            ("@{ $k = 1 }", "HASH_KEY"),
            ('@{ "a$b" = 1 }', "HASH_KEY"),
            ("@{ (1+1) = 1 }", "HASH_KEY"),
        ]
    )


def test_index_of_is_no_longer_an_allowed_method():
    # X509Certificate2Collection.IndexOf는 문자열 인자를 인증서 파일 경로로 읽는다
    ok, found = codes("(Get-Item Cert:\\LocalMachine\\My).Certificates.IndexOf('\\\\z\\c$\\x.cer')")
    assert not ok and "METHOD" in found


def test_a_script_block_may_only_be_written_where_a_shaping_command_takes_it():
    # 지연 바인딩은 값의 형식으로 정해진다. 블록이 괄호, 배열, 인덱스, 해시테이블, $( )를 거쳐 와도 같다
    # (2026-11-11 이 PC에서 Get-Date -Date로 측정)
    assert_each_line_refused(
        [
            ("'x' | Get-Content -Path (({ 'C:\\x' }, 1)[0])", "SCRIPT_BLOCK_ARG"),
            ("'x' | Get-Content -Path @({ 'C:\\x' })[0]", "SCRIPT_BLOCK_ARG"),
            ("'x' | Get-Content -Path (,{ 'C:\\x' })[0]", "SCRIPT_BLOCK_ARG"),
            ("'x' | Get-Content -Path (@{ a = { 'C:\\x' } }).a", "SCRIPT_BLOCK_ARG"),
            ("'x' | Get-Content -Path $(1 | Out-Null; { 'C:\\x' })", "SCRIPT_BLOCK_ARG"),
            ("'x' | Get-Content -Path $(if (1) { { 'C:\\x' } })", "SCRIPT_BLOCK_ARG"),
            ("$b = { 1 }", "SCRIPT_BLOCK_ARG"),
            ("'a,b' -split { $_ -eq ',' }", "SCRIPT_BLOCK_ARG"),
            ("Get-Process | Where-Object ({ $_.Id -gt 4 })", "SCRIPT_BLOCK_ARG"),
            ("'x' | Get-Content -Path { '\\\\z\\c$\\x' }", "SCRIPT_BLOCK_ARG"),
            ("'x' | Get-Content -Path ({ '\\\\z\\c$\\x' })", "SCRIPT_BLOCK_ARG"),
            ("'x' | Get-Content -Path $({ '\\\\z\\c$\\x' })", "SCRIPT_BLOCK_ARG"),
        ]
    )


def test_a_catch_type_is_a_plain_dotted_name():
    # 어셈블리를 적은 catch 타입은 파서가 그 어셈블리를 찾게 한다
    ok, found = codes("try { 1 } catch [T, hqprobe9x] { 2 }")
    assert not ok and "TYPE" in found


def test_pscustomobject_is_written_only_in_front_of_a_literal_table():
    # 실행 중에 만든 표($Matches)를 바꾸면 이름 붙은 그룹 PSTypeName이 키 노릇을 한다(2026-11-11 검토자 측정)
    rows = "$null = 'System.IO.FileInfo|\\\\z\\c$\\x.dll' -match '(?<PSTypeName>[^|]+)\\|(?<FullName>.+)'"
    assert_each_line_refused(
        [
            (f"{rows}; ([pscustomobject]$Matches).VersionInfo", "TYPE"),
            (f"{rows}; [pscustomobject]$Matches | Select-Object VersionInfo", "TYPE"),
            (
                "switch -Regex ('System.IO.FileInfo|\\\\z\\c$\\x.dll') { '(?<PSTypeName>[^|]+)\\|(?<FullName>.+)' "
                "{ ([pscustomobject]$Matches).VersionInfo } }",
                "TYPE",
            ),
            (
                "('System.Management.ManagementObject#root\\cimv2\\Win32_Ping' + 'Status|x.example') -match "
                "'(?<PSTypeName>[^|]+)\\|(?<Address>.+)'; ([pscustomobject]$Matches).IPV4Address",
                "TYPE",
            ),
        ]
    )


def test_pscustomobject_of_a_variable_or_parentheses_is_refused_as_the_price():
    assert_each_line_refused([("[pscustomobject]$h", "TYPE"), ("[pscustomobject](@{ a = 1 })", "TYPE")])


# 변수가 섞인 문자열은 글자 부분(안쪽 식 밖의 글자, 따옴표 빼고, 백틱 지우고)으로 경로를 본다. 맨 앞의 빈 펼침
# ($null, $())은 아무것도 내지 않으므로 그 뒤의 글자 부분이 경로의 시작이다
@pytest.mark.parametrize(
    "script",
    [
        'Get-Content "$null\\\\z\\c$\\x"',
        'Get-Content "$()\\\\z\\c$\\x"',
        'Get-Content "${a}//z/c$/x"',
        'Get-Content "$a$b\\\\z\\c$\\x"',
        "Get-Content $null\\\\z\\c$\\x",
        'Get-Content "$null`\\\\z\\c$\\x"',
        'Get-Content "$null\\`\\z\\c$\\x"',
        'Get-Content "\\\\$h\\c$"',
        'Get-Content "$()FileSystem::\\\\z\\c$\\x"',
        'Get-Content "$()function:\\x"',
        'Get-Content @"\n$null\\\\z\\c$\\x\n"@',
        'Get-Content "$null\u00ad\\\\z\\c$\\x"',
    ],
)
def test_a_path_after_empty_expansions_in_a_mixed_string_is_refused(script):
    ok, found = codes(script)
    assert not ok and "PATH" in found


# 글자 부분은 원본이 아니라 엔진이 실행할 때 쓰는 틀 문자열(FormatExpression)에서 얻는다. 맨 글자 인자는 따옴표 친
# 조각의 따옴표를 토크나이저가 지우고, here-string은 CR 하나도 줄바꿈으로 받으며, `t는 탭이 된다
@pytest.mark.parametrize(
    "script",
    [
        'Get-Content $null""\\\\z\\c$\\x',
        'Get-Content $null"\\\\z\\c$\\x"',
        "Get-Content $null'\\\\z\\c$\\x'",
        "Get-ChildItem -Path $null''\\\\z\\c$",
        'Get-Content -Path:$null""\\\\z\\c$\\x',
        'Get-Content x, $null""\\\\z\\c$\\x',
        '1 | ForEach-Object { Get-Content $null""\\\\z\\c$\\x }',
        'Get-Content ${a}""\\\\z\\c$\\x',
        'Get-Content $null""//z/c$/x',
        'Get-Content $null"FileSystem:":\\\\z\\c$\\x',
        'Get-Content $null""FileSystem:"":\\\\z\\c$\\x',
        'Get-Content $null"function:\\x"',
        "Get-Content $null'variable:\\x'",
        'Get-Content @"\r\\\\z\\c$\\x\n$null\n\r"@',
        'Get-Content @"\r\\\\z\\c$\\x\n$null\n"@',
        'Get-Content @"\n$null\n\\\\z\\c$\\x\r"@',
        'Get-Content "$null`t\\\\z\\c$\\x"',
    ],
)
def test_a_path_is_judged_by_the_format_string_the_engine_uses(script):
    ok, found = codes(script)
    assert not ok and "PATH" in found


def test_literal_braces_and_an_escaped_dollar_in_a_mixed_string_pass():
    # 틀 문자열에서 글자 중괄호는 겹쳐 있다({{0}}). `$는 글자 $라 값이 $로 시작한다
    script = "\n".join(['Write-Output "{0} $x {1}"', 'Write-Output "`$null\\\\z$null"'])
    verdict = check_query(script)
    assert (verdict.ok, verdict.reasons) == (True, ())


# 앞의 공백(U+0020)은 지우고 본다. PowerShell의 경로 해석은 ' \\z\c$\x'를 현재 위치 기준 경로로 보지만 .NET은 UNC
# 경로로 보므로 글자를 .NET에 그대로 넘기는 명령은 그 컴퓨터에 닿는다(2026-11-11 이 PC에서 글자 처리만으로 측정).
# 공백뿐인 글자 부분은 빈 것으로 본다. 탭은 보이지 않는 글자를 지운 사본에서 사라진다
@pytest.mark.parametrize(
    "script",
    [
        "Get-Content ' \\\\z\\c$\\x'",
        "Get-Content '   //z/c$/x'",
        'Get-Content " \\\\$h\\c$"',
        'Get-Content "$null \\\\z\\c$\\x"',
        'Get-Content " $null\\\\z\\c$\\x"',
        "Get-Content ' function:\\x'",
        "Get-Content $null' '\\\\z\\c$\\x",
        "Get-Content '\t \\\\z\\c$\\x'",
    ],
)
def test_a_path_after_leading_spaces_is_refused(script):
    ok, found = codes(script)
    assert not ok and "PATH" in found


def test_strings_that_only_start_with_spaces_pass():
    script = "\n".join(
        ["Get-ChildItem ' C:\\x'", "Write-Output ' \\ x'", 'Get-ChildItem "$a \\$b"', 'Write-Output "  $x  "']
    )
    verdict = check_query(script)
    assert (verdict.ok, verdict.reasons) == (True, ())


# NT 이름 공간 표기. .NET의 GetFullPath는 \??\UNC\z\c$\x를 고치지 않고 그대로 두고 Windows는 이것을 \\z\c$\x와 같은
# 경로로 읽는다(2026-11-12 측정). 같은 앞부분의 로컬 경로(\??\C:\x)도 거절한다. 규칙을 단순하게 둔 값이다
@pytest.mark.parametrize(
    "script",
    [
        "Get-Content '\\??\\UNC\\z\\c$\\x'",
        "Get-Content ' \\??\\UNC\\z\\c$\\x'",
        "Get-Content -LiteralPath '\\??\\UNC\\z\\c$\\x'",
        'Get-Content "$null\\??\\UNC\\z\\c$\\x"',
        "Get-Content '\\??\\C:\\x'",
        "Get-Content '/??/UNC/z/c$/x'",
    ],
)
def test_a_path_with_the_nt_namespace_prefix_is_refused(script):
    ok, found = codes(script)
    assert not ok and "PATH" in found


def test_question_marks_elsewhere_in_a_path_pass():
    # 물음표 둘 뒤에 구분자가 없거나(\??x는 -like의 패턴) 경로의 맨 앞이 아니면 그 표기가 아니다
    script = "\n".join(["Get-ChildItem 'C:\\x\\??'", "Write-Output '\\?'", "Where-Object Name -like '\\??x'"])
    verdict = check_query(script)
    assert (verdict.ok, verdict.reasons) == (True, ())


def test_the_controls_of_the_second_review_pass():
    # 펼침 안의 코드는 경로 글자로 읽지 않는다. [pscustomobject]는 글자로 적은 표 앞과 -as 오른쪽에 쓸 수 있다
    script = "\n".join(
        [
            'Write-Output "$([math]::Round(1.5, 1)) GB"',
            "Write-Output \"Total: $([string]::Format('{0:N1}', 1.5))\"",
            'Get-ChildItem "$env:SystemRoot\\$name\\x"',
            'Get-ItemProperty "Registry::HKEY_USERS\\$sid\\Software"',
            'Get-Content "C:\\x\\$a"',
            "[pscustomobject]@{ Name = 'x' }",
            "[pscustomobject][ordered]@{ a = 1; b = 2 }",
            "$x -as [pscustomobject]",
        ]
    )
    verdict = check_query(script)
    assert (verdict.ok, verdict.reasons) == (True, ())


def test_the_resource_uri_of_get_ciminstance_is_a_remote_parameter():
    # 문서상 WS-Man 세션에서만 쓰인다. 이름 검사는 URI의 %5F를 풀지 않는다
    assert_each_line_refused(
        [
            ("Get-CimInstance -ResourceUri http://x.example/wbem/wscim/1/cim-schema/2/Win32%5FProduct", "PARAM_REMOTE"),
            ("Get-CimInstance Win32_OperatingSystem -Res x", "PARAM_REMOTE"),
        ]
    )


def test_the_controls_of_the_expression_review_pass():
    script = "\n".join(
        [
            'Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" -Property DeviceID, Size',
            "Get-CimInstance -ClassName Win32_OperatingSystem -OperationTimeoutSec 5",
            "Get-Service -Name spooler",
            "Get-Process -Id 4",
            "$event = Get-Date",
            "Get-Process | Select-Object Name, @{ n = 'x'; e = { $_.Name } }",
            "$h = @{ 'a b' = 1; 2 = 'x' }",
            "[pscustomobject]@{ Name = 'x' }",
            "Get-Process | Sort-Object { $_.Id }, { $_.Name }",
            "Get-Process | Select-Object Name, @{ n = 'M'; e = { $_.WS / 1MB } }",
            "Get-Process | Format-Table Name, { $_.Id }",
            "Get-Process | Where-Object -FilterScript:{ $_.Id -gt 4 }",
            "Get-Process | ForEach-Object -Begin { $n = 0 } -Process { $n++ } -End { $n }",
            "switch (5) { { $_ -gt 3 } { 'big' } }",
            "try { 1 } catch [System.UnauthorizedAccessException] { 2 }",
        ]
    )
    verdict = check_query(script)
    assert (verdict.ok, verdict.reasons) == (True, ())


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
    assert_each_line_refused(lines)


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
    assert_each_line_refused(lines)


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


# 검사 템플릿이 경로 규칙에 쓰는 비공개 속성. 이 PC의 PowerShell에서 사라지거나 바뀌면 여기서 먼저 알린다
FORMAT_EXPRESSION = r"""
$ErrorActionPreference = 'Stop'
$property = [System.Management.Automation.Language.ExpandableStringExpressionAst].GetProperty(
    'FormatExpression', [System.Reflection.BindingFlags]'Instance,NonPublic')
$tree = [System.Management.Automation.Language.Parser]::ParseInput('"$null\\z"', [ref]$null, [ref]$null)
$string = $tree.Find({ param($n) $n -is [System.Management.Automation.Language.ExpandableStringExpressionAst] }, $true)
[Console]::Out.Write($property.GetValue($string))
"""


def test_the_engine_still_gives_the_format_string_the_path_rule_reads():
    result = run_ps(FORMAT_EXPRESSION)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "{0}\\\\z"


def test_the_check_template_parses_and_never_runs_the_script(tmp_path):
    text = (ROOT / "src/hq/ps/mcp-check.ps1").read_text(encoding="utf-8")
    assert parse_errors(text, tmp_path) == []
    for word in ("Invoke-Expression", "iex ", ".Invoke(", "GetScriptBlock", "SafeGetValue", "Invoke-Command"):
        assert word not in text, word
    marker = tmp_path / "ran.txt"
    check_query(f"Set-Content -LiteralPath '{marker}' -Value x")
    assert not marker.exists()
