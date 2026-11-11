"""이 PC의 명령 정보와 표본 객체를 훑어 조회 검사의 허용 목록이 빠뜨린 것을 찾는다(스펙 15장 매개변수와 메서드 훑기).

매개변수 훑기는 명령 정보(Get-Command의 매개변수 특성과 별칭)만 읽고 명령을 실행하지 않는다. 메서드 훑기는 허용 목록의
조회 명령을 이 PC에서 인자 없이(인자가 필요한 것은 아래 표본 인자로) 불러 결과 객체의 메서드 이름만 보고, 메서드는
부르지 않는다. WMI는 클래스 정의(Get-CimClass)만 읽는다. 조회 검사에 넘기는 스크립트는 여기서 쓰지 않는다.
"""

import json

import pytest
from ps import run_ps

from hq.mcp.querycheck import load_rules

pytestmark = pytest.mark.integration


def _scan(script: str, payload: dict, *, timeout: float = 100):
    """훑기 스크립트를 돌려 표준 출력의 JSON을 돌려준다. 값은 환경 변수 HQ_SCAN_INPUT에 ASCII JSON으로 넘긴다."""
    result = run_ps(script, env={"HQ_SCAN_INPUT": json.dumps(payload, ensure_ascii=True)}, timeout=timeout)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


REMOTE_ALIASES = r"""
$in = $env:HQ_SCAN_INPUT | ConvertFrom-Json
$found = [ordered]@{}
foreach ($name in $in.commands) {
    foreach ($command in @(Get-Command -Name $name -ErrorAction SilentlyContinue)) {
        $aliases = @(foreach ($parameter in $command.Parameters.Values) {
            if ($in.remote_params -contains $parameter.Name) { $parameter.Aliases }
        })
        if ($aliases.Count -gt 0) { $found[$name] = $aliases }
    }
}
ConvertTo-Json -InputObject $found -Compress
"""


def remote_aliases_on_this_pc(commands: list[str], remote_params: list[str]) -> dict[str, list[str]]:
    """허용 명령마다 이름이 remote_params인 매개변수의 별칭. {"Get-CimInstance": ["CN", "ServerName"], ...}"""
    return _scan(REMOTE_ALIASES, {"commands": commands, "remote_params": remote_params})


def test_every_alias_of_a_remote_parameter_is_on_the_list():
    live = remote_aliases_on_this_pc(load_rules()["commands"], load_rules()["remote_params"])
    assert "Get-CimInstance" in live  # 훑기가 아무것도 찾지 못하면 아래 검사가 저절로 통과하므로 먼저 막는다
    rules = load_rules()
    known = {
        c.lower(): {a.lower() for a in rules["remote_param_aliases"].get(c, [])}
        | {p.lower() for p in rules["remote_params"]}
        for c in live
    }
    missing = {c: sorted({a.lower() for a in aliases} - known[c.lower()]) for c, aliases in live.items()}
    assert {c: m for c, m in missing.items() if m} == {}


# 인자가 필요한 명령은 계획의 표본 인자로 부르고 인자 없이는 부르지 않는다. Get-PSDrive는 인자 없이 부르면 연결된
# 네트워크 드라이브의 남은 공간을 물으므로 C만 본다. Get-AuthenticodeSignature는 계획의 notepad.exe 대신 서명 없는
# win.ini로 부른다. 서명이 있으면 인증서 체인을 검증하며 해지 목록을 받으러 나갈 수 있고, 결과 객체의 형식(Signature)은
# 같다
METHOD_NAMES = r"""
$in = $env:HQ_SCAN_INPUT | ConvertFrom-Json
$hits = New-Object System.Collections.Generic.List[string]
function Add-Hits($objects) {
    foreach ($object in $objects) {
        if ($null -eq $object) { continue }
        $type = $object.GetType().FullName
        foreach ($member in @(Get-Member -InputObject $object -MemberType Method, ScriptMethod, CodeMethod)) {
            if ($in.methods -contains $member.Name) { $hits.Add($type + '|' + $member.Name) }
        }
    }
}
$samples = [ordered]@{
    'Get-Item' = { Get-Item $env:windir; Get-Item HKLM:\SOFTWARE }
    'Get-ItemProperty' = { Get-ItemProperty HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion }
    'Get-Content' = { Get-Content $env:windir\win.ini -TotalCount 1 }
    'Select-String' = { Select-String -Path $env:windir\win.ini -Pattern a }
    'Get-FileHash' = { Get-FileHash $env:windir\win.ini }
    'Get-AuthenticodeSignature' = { Get-AuthenticodeSignature $env:windir\win.ini }
    'Get-CimInstance' = { Get-CimInstance Win32_OperatingSystem }
    'Get-WinEvent' = { Get-WinEvent -LogName System -MaxEvents 1 }
    'Get-EventLog' = { Get-EventLog -LogName System -Newest 1 }
    'Get-PSDrive' = { Get-PSDrive C }
    'Get-Date' = { Get-Date }
}
foreach ($name in $samples.Keys) {
    try { Add-Hits @(& $samples[$name] | Select-Object -First 3) } catch { }
}
# 식으로 만드는 표본. XmlDocument는 파이프라인에서 자식 노드로 풀리므로 배열에 하나로 담아 넘긴다
Add-Hits @(, [regex]'a')
Add-Hits @(, [xml]'<a/>')
foreach ($name in $in.commands) {
    if (-not $name.StartsWith('Get-', [StringComparison]::Ordinal) -or $samples.Contains($name)) { continue }
    try { Add-Hits @(& $name -ErrorAction Stop | Select-Object -First 3) } catch { }
}
foreach ($namespace in 'root/cimv2', 'root/StandardCimv2', 'root/Microsoft/Windows/Storage') {
    foreach ($class in @(Get-CimClass -Namespace $namespace -ErrorAction SilentlyContinue)) {
        foreach ($method in $class.CimClassMethods) {
            if ($in.methods -contains $method.Name) {
                $hits.Add('CimClass#' + $namespace + '/' + $class.CimClassName + '|' + $method.Name)
            }
        }
    }
}
ConvertTo-Json -InputObject @($hits | Sort-Object -Unique) -Compress
"""


def method_name_hits(instance_methods: list[str]) -> set[tuple[str, str]]:
    """표본 객체와 WMI 클래스에서 이름이 instance_methods에 있는 메서드. {(형식 전체 이름, 메서드 이름), ...}"""
    commands = load_rules()["commands"]
    found = _scan(METHOD_NAMES, {"commands": commands, "methods": instance_methods}, timeout=540)
    return {tuple(hit.split("|", 1)) for hit in found}


# 표본 명령을 차례로 부른다(Get-ComputerInfo, Get-Counter, WMI 클래스 정의 읽기가 오래 걸린다)
@pytest.mark.timeout(600)
def test_no_allowed_instance_method_name_exists_on_an_unapproved_type():
    hits = method_name_hits(load_rules()["instance_methods"])
    assert ("System.String", "Trim") in hits  # 표본을 하나도 보지 못하면 아래 검사가 저절로 통과하므로 먼저 막는다
    approved_types = {"System.String", "System.DateTime", "System.Text.RegularExpressions.Regex"}
    outside = {(t, m) for t, m in hits if t not in approved_types and m != "ToString"}
    assert outside <= {("System.Diagnostics.Eventing.Reader.EventLogRecord", "ToXml")}


# 원격 매개변수가 파이프라인 입력으로도 묶이는 명령
PIPELINE_REMOTE = r"""
$in = $env:HQ_SCAN_INPUT | ConvertFrom-Json
$rules = $in.rules
$base = @($rules.remote_params | Where-Object { $_ -ne 'Credential' })
$found = New-Object System.Collections.Generic.List[string]
foreach ($name in $rules.commands) {
    $remote = $base
    if ($rules.remote_param_aliases.PSObject.Properties[$name]) {
        $remote = $remote + @($rules.remote_param_aliases.$name)
    }
    foreach ($command in @(Get-Command -Name $name -ErrorAction SilentlyContinue)) {
        foreach ($parameter in $command.Parameters.Values) {
            $labels = @($parameter.Name) + @($parameter.Aliases)
            $isRemote = @($labels | Where-Object { $remote -contains $_ }).Count -gt 0
            $piped = @($parameter.ParameterSets.Values |
                Where-Object { $_.ValueFromPipeline -or $_.ValueFromPipelineByPropertyName }).Count -gt 0
            if ($isRemote -and $piped) { $found.Add($name) }
        }
    }
}
ConvertTo-Json -InputObject @($found | Sort-Object -Unique) -Compress
"""


def test_every_command_that_binds_a_remote_parameter_from_the_pipeline_is_listed():
    found = _scan(PIPELINE_REMOTE, {"rules": load_rules()})
    assert "Get-CimInstance" in found  # 훑기가 아무것도 찾지 못하면 아래 검사가 저절로 통과하므로 먼저 막는다
    assert sorted(set(found) - set(load_rules()["pipeline_remote_commands"])) == []


# 스크립트 블록을 인자로 받아도 되는 명령의 파이프라인 매개변수. 다른 명령에 준 블록은 지연 바인딩되어 파이프라인
# 입력마다 실행되고 그 결과가 매개변수 값(경로, 이름)이 된다. 목록의 명령은 파이프라인으로 입력 객체만 받으므로 블록의
# 결과도 다음에 다듬을 객체가 될 뿐이다
PIPELINE_PARAMETERS = r"""
$in = $env:HQ_SCAN_INPUT | ConvertFrom-Json
$found = [ordered]@{}
foreach ($name in $in.commands) {
    foreach ($command in @(Get-Command -Name $name -ErrorAction SilentlyContinue)) {
        $found[$name] = @(foreach ($parameter in $command.Parameters.Values) {
            $piped = @($parameter.ParameterSets.Values |
                Where-Object { $_.ValueFromPipeline -or $_.ValueFromPipelineByPropertyName }).Count -gt 0
            if ($piped) { $parameter.ParameterType.FullName }
        })
    }
}
ConvertTo-Json -InputObject $found -Compress
"""


def test_a_command_that_may_take_a_script_block_takes_only_input_objects_from_the_pipeline():
    rules = load_rules()
    listed = rules["script_block_commands"]
    assert set(listed) <= set(rules["commands"])
    found = _scan(PIPELINE_PARAMETERS, {"commands": listed})
    assert sorted(found) == sorted(listed)
    objects = {
        "System.Management.Automation.PSObject",
        "System.Management.Automation.PSObject[]",
        "System.Object",
        "System.Object[]",
    }
    assert {name: sorted(set(types) - objects) for name, types in found.items() if set(types) - objects} == {}
