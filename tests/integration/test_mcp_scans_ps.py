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
$inspected = New-Object System.Collections.Generic.List[string]
foreach ($name in $in.commands) {
    foreach ($command in @(Get-Command -Name $name -ErrorAction SilentlyContinue)) {
        $inspected.Add($name)
        $aliases = @(foreach ($parameter in $command.Parameters.Values) {
            if ($in.remote_params -contains $parameter.Name) { $parameter.Aliases }
        })
        if ($aliases.Count -gt 0) { $found[$name] = $aliases }
    }
}
ConvertTo-Json -InputObject ([ordered]@{ found = $found; inspected = @($inspected) }) -Compress -Depth 4
"""


def remote_aliases_on_this_pc(commands: list[str], remote_params: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """허용 명령마다 이름이 remote_params인 매개변수의 별칭({"Get-CimInstance": ["CN", "ServerName"], ...})과
    이 PC에서 명령 정보를 읽은 명령의 이름."""
    result = _scan(REMOTE_ALIASES, {"commands": commands, "remote_params": remote_params})
    return result["found"], result["inspected"]


def test_every_alias_of_a_remote_parameter_is_on_the_list():
    live, inspected = remote_aliases_on_this_pc(load_rules()["commands"], load_rules()["remote_params"])
    # 훑기가 명령을 읽지 못하면 아래 검사가 저절로 통과하므로 먼저 막는다. 이 PC에는 허용 명령이 모두 있다(2026-11-11)
    assert sorted(inspected) == sorted(load_rules()["commands"])
    assert "Get-CimInstance" in live
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
$types = New-Object System.Collections.Generic.List[string]
function Add-Hits($objects) {
    foreach ($object in $objects) {
        if ($null -eq $object) { continue }
        $type = $object.GetType().FullName
        $types.Add($type)
        foreach ($member in @(Get-Member -InputObject $object -MemberType Method, ScriptMethod, CodeMethod)) {
            if ($in.methods -contains $member.Name) { $hits.Add($type + '|' + $member.Name) }
        }
    }
}
$samples = [ordered]@{
    'Get-Item' = { Get-Item $env:windir; Get-Item HKLM:\SOFTWARE; Get-Item Cert:\LocalMachine\My }
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
# 식으로 만드는 표본. XmlDocument와 인증서 묶음은 파이프라인에서 낱개로 풀리므로 배열에 하나로 담아 넘긴다.
# 인증서 묶음은 이 PC의 로컬 저장소에서 읽는다(파일에서 인증서를 불러오지 않는다)
Add-Hits @(, [regex]'a')
Add-Hits @(, [xml]'<a/>')
Add-Hits @(, (Get-Item Cert:\LocalMachine\My).Certificates)
foreach ($name in $in.commands) {
    if (-not $name.StartsWith('Get-', [StringComparison]::Ordinal) -or $samples.Contains($name)) { continue }
    try { Add-Hits @(& $name -ErrorAction Stop | Select-Object -First 3) } catch { }
}
foreach ($namespace in 'root/cimv2', 'root/StandardCimv2', 'root/Microsoft/Windows/Storage') {
    $classes = @(Get-CimClass -Namespace $namespace -ErrorAction SilentlyContinue)
    if ($classes.Count -gt 0) { $types.Add('CimClass#' + $namespace) }
    foreach ($class in $classes) {
        foreach ($method in $class.CimClassMethods) {
            if ($in.methods -contains $method.Name) {
                $hits.Add('CimClass#' + $namespace + '/' + $class.CimClassName + '|' + $method.Name)
            }
        }
    }
}
$result = [ordered]@{ hits = @($hits | Sort-Object -Unique); types = @($types | Sort-Object -Unique) }
ConvertTo-Json -InputObject $result -Compress
"""


def method_name_hits(instance_methods: list[str]) -> tuple[set[tuple[str, str]], set[str]]:
    """표본 객체와 WMI 클래스에서 이름이 instance_methods에 있는 메서드({(형식 전체 이름, 메서드 이름), ...})와
    표본으로 본 형식의 전체 이름(WMI는 읽은 네임스페이스마다 CimClass#<네임스페이스>)."""
    commands = load_rules()["commands"]
    found = _scan(METHOD_NAMES, {"commands": commands, "methods": instance_methods}, timeout=540)
    return {tuple(hit.split("|", 1)) for hit in found["hits"]}, set(found["types"])


# 표본이 하나라도 소리 없이 실패하면 알도록 이 PC에서 본 형식을 최소한으로 적어 둔다(2026-11-11)
SAMPLED_AT_LEAST = {
    "System.IO.DirectoryInfo",
    "Microsoft.Win32.RegistryKey",
    "System.Security.Cryptography.X509Certificates.X509Store",
    "System.Security.Cryptography.X509Certificates.X509Certificate2Collection",
    "System.Management.Automation.PSCustomObject",
    "System.String",
    "Microsoft.PowerShell.Commands.MatchInfo",
    "System.Management.Automation.Signature",
    "Microsoft.Management.Infrastructure.CimInstance",
    "System.Diagnostics.Eventing.Reader.EventLogRecord",
    "System.Diagnostics.EventLogEntry",
    "System.Management.Automation.PSDriveInfo",
    "System.DateTime",
    "System.Text.RegularExpressions.Regex",
    "System.Xml.XmlDocument",
    "System.Diagnostics.Process",
    "System.ServiceProcess.ServiceController",
    "CimClass#root/cimv2",
    "CimClass#root/StandardCimv2",
    "CimClass#root/Microsoft/Windows/Storage",
}

# 승인한 형식(글자, 날짜, 정규식) 밖에서 허용 이름과 겹쳐도 받는 메서드와 그 까닭. ToString은 어디서나 받는다
ACCEPTED_OUTSIDE = {
    ("System.Diagnostics.Eventing.Reader.EventLogRecord", "ToXml"): "이벤트를 XML 글자로 읽기만 한다",
    ("System.Security.Cryptography.X509Certificates.X509Certificate2Collection", "Contains"): (
        "문자열 인자는 오버로드가 모호해 실패한다(2026-11-11 검토자 측정)"
    ),
}


# 표본 명령을 차례로 부른다(Get-ComputerInfo, Get-Counter, WMI 클래스 정의 읽기가 오래 걸린다)
@pytest.mark.timeout(600)
def test_no_allowed_instance_method_name_exists_on_an_unapproved_type():
    hits, types = method_name_hits(load_rules()["instance_methods"])
    assert sorted(SAMPLED_AT_LEAST - types) == []  # 표본이 실패하면 아래 검사가 저절로 통과하므로 먼저 막는다
    assert ("System.String", "Trim") in hits
    approved_types = {"System.String", "System.DateTime", "System.Text.RegularExpressions.Regex"}
    outside = {(t, m) for t, m in hits if t not in approved_types and m != "ToString"}
    assert sorted(outside - set(ACCEPTED_OUTSIDE)) == []


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


# 해시테이블로 만들 수 있는 매개변수 타입: 원시 타입, 문자열, 열거형, object, psobject, hashtable, scriptblock, switch가
# 아니고, 인자 없는 공개 생성자와 쓸 수 있는 공개 속성이 있는 타입(배열이면 요소 타입). 바인더는 해시테이블을 그런
# 타입의 개체로 바꿔 속성을 채운다(Get-Service -InputObject @{ MachineName = ... }). 명령 정보와 리플렉션만 읽는다
HASHTABLE_BUILDABLE = r"""
$in = $env:HQ_SCAN_INPUT | ConvertFrom-Json
$plain = [type[]]@([string], [object], [psobject], [hashtable], [scriptblock],
    [System.Management.Automation.SwitchParameter])
$found = New-Object System.Collections.Generic.List[string]
foreach ($name in $in.commands) {
    foreach ($command in @(Get-Command -Name $name -ErrorAction SilentlyContinue)) {
        foreach ($parameter in $command.Parameters.Values) {
            $type = $parameter.ParameterType
            if ($type.IsArray) { $type = $type.GetElementType() }
            if ($type.IsPrimitive -or $type.IsEnum -or $plain -contains $type) { continue }
            if ($null -eq $type.GetConstructor([type[]]@())) { continue }
            $settable = @($type.GetProperties() | Where-Object { $_.CanWrite -and $null -ne $_.GetSetMethod() })
            if ($settable.Count -gt 0) { $found.Add($name + '|' + $parameter.Name + '|' + $type.FullName) }
        }
    }
}
ConvertTo-Json -InputObject @($found) -Compress
"""

# 이 PC에서 찾은 것(2026-11-11)과 받아도 되는 까닭. 목록에 없는 것이 나오면 판단을 거쳐 여기에 더한다
BUILDABLE = {
    ("Get-Service", "InputObject", "System.ServiceProcess.ServiceController"): (
        "원격 매개변수로 거절한다(PARAM_REMOTE). 해시테이블로 MachineName을 정할 수 있다"
    ),
    ("Get-Process", "InputObject", "System.Diagnostics.Process"): (
        "원격 매개변수로 거절한다(PARAM_REMOTE). 해시테이블로 만든 프로세스 개체에 기대지 않는다"
    ),
    ("Get-LocalGroupMember", "Group", "Microsoft.PowerShell.Commands.LocalGroup"): (
        "이 PC의 로컬 그룹을 이름이나 SID로 찾을 뿐이다. 다른 컴퓨터로 가는 속성이 없다"
    ),
    ("Get-WinEvent", "FilterXml", "System.Xml.XmlDocument"): (
        "해시테이블은 XmlDocument로 바뀌지 않는다(2026-11-11 측정). 문자열은 [xml]처럼 바뀌어 외부 엔터티를 읽지 않는다"
    ),
}


def test_every_parameter_type_a_hashtable_can_build_is_known():
    found = _scan(HASHTABLE_BUILDABLE, {"commands": load_rules()["commands"]})
    assert {tuple(entry.split("|")) for entry in found} == set(BUILDABLE)
