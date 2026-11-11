$hqSessionVariables = [string[]]((Get-Variable).Name -ne 'hqTemplate')
# 위 줄은 맨 첫 줄이어야 한다. 템플릿이 변수를 만들기 전이므로 새 세션의 변수 목록이다(로더가 만든 hqTemplate만 뺀다).
#
# 조회 검사(스펙 7장). 스크립트를 이 PC의 파서로 문법 트리로만 만들어 허용 목록과 견준다. 스크립트의 어느 부분도
# 실행하지 않는다: 스크립트 블록으로 만들지 않고, 명령을 찾아보지 않으며, 트리에서 값을 계산하지 않는다.
# 트리 종류도 허용 목록이다. $hqKinds에 없는 종류는 NODE로 거절한다. 이름 비교는 모두 대소문자를 무시한다.
#
# 비교 규칙: 검사하는 스크립트에서 온 글자는 문화권 규칙으로 비교하지 않는다. PowerShell의 -eq, -like, -match,
# -contains, -in, switch, @{} 키 찾기와 StringComparison 없는 StartsWith, IndexOf 등은 언어 규칙으로 비교해 소프트
# 하이픈 같은 보이지 않는 글자를 무시한다(매개변수 바인더도 그렇다. 2026-11-11 측정). 그래서 [string]::Equals와
# StringComparison.Ordinal(IgnoreCase), 그것으로 만든 HashSet과 Dictionary, 대소문자를 구별하는 [regex]만 쓴다.
# 글 속에서 이름을 찾아 막는 규칙(WMI 클래스, 경로 앞부분, UNC)은 출력 가능한 ASCII 밖의 글자를 지운 사본에도 견준다.
# 변수, 멤버(속성과 메서드), 형식의 이름은 출력 가능한 ASCII만 받고, 그 밖의 글자가 있으면 목록과 견주기 전에 거절한다.
#
# 이유 코드: PARSE_ERROR NODE DEFINITION INVOKE COMMAND EXTERNAL_ARGS PIPELINE_INPUT PARAM_NAME PARAM_REMOTE
# PARAM_VARIABLE FOREACH_FORM SCRIPT_BLOCK_ARG PATH SPLAT REDIRECT WMI_CLASS VARIABLE USING ASSIGN AS TYPE METHOD
# SWITCH_FILE HASH_KEY. Python이 PARSER_HAZARD와 CHECK_FAILED를 더한다.
#
# 입력(표준 입력, ASCII JSON): {"script", "rules"}. rules는 src/hq/mcp/query_rules.json이다.
# 출력: 표준 출력에 {"ok", "reasons": [{"code", "message", "line"}]} 한 줄을 쓰고 종료 코드 0으로 끝난다.
# 로더가 PowerShell 스트림을 모두 버리므로 [Console]::Out으로만 쓴다. 외부 프로그램과 $Host.UI는 쓰지 않는다.
# 잡지 않은 오류(StrictMode 위반 포함)는 로더가 종료 코드 70으로 끝내고, Python은 이것을 검사 실패로 보고 거절한다.

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$hqInput = [Console]::In.ReadToEnd() | ConvertFrom-Json
$hqRules = $hqInput.rules

function New-HqSet($items) {
    # 대소문자를 무시하는 이름 모음(서수 비교)
    $set = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($item in $items) { [void]$set.Add([string]$item) }
    return , $set
}

function New-HqMap($object) {
    # JSON 객체를 대소문자를 무시하는 사전으로(서수 비교)
    $map = [System.Collections.Generic.Dictionary[string, object]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($property in $object.PSObject.Properties) { $map.Add($property.Name, $property.Value) }
    return , $map
}

$hqCommands = New-HqSet $hqRules.commands
$hqAliases = New-HqMap $hqRules.aliases
$hqExternal = New-HqMap $hqRules.external
$hqRemoteParams = [string[]]@($hqRules.remote_params)
$hqRemoteAliases = New-HqMap $hqRules.remote_param_aliases
$hqPipelineRemote = New-HqSet $hqRules.pipeline_remote_commands
$hqVariableParams = [string[]]@($hqRules.variable_params)
# 넣을 수 있는 변수 이름: (새 세션의 변수 ∪ extra_protected_variables) − assignable_exceptions에 없고 :이 없다
$hqProtectedVariables = New-HqSet ($hqSessionVariables + @($hqRules.extra_protected_variables))
foreach ($hqName in $hqRules.assignable_exceptions) { [void]$hqProtectedVariables.Remove([string]$hqName) }
$hqWmiClasses = [string[]]@($hqRules.blocked_wmi_classes)
$hqPathPrefixes = [string[]]@($hqRules.blocked_path_prefixes)
$hqBlockedReads = New-HqSet $hqRules.blocked_read_variables
$hqTypes = New-HqSet $hqRules.types
$hqInstanceMethods = New-HqSet $hqRules.instance_methods
$hqStaticMethods = [System.Collections.Generic.Dictionary[string, object]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($hqProperty in $hqRules.static_methods.PSObject.Properties) {
    $hqStaticMethods.Add($hqProperty.Name, (New-HqSet $hqProperty.Value))
}
# 스크립트 블록을 인자로 받아도 되는 명령. 파이프라인으로 입력 객체만 받으므로 블록이 지연 바인딩되어도 그 결과는
# 다듬을 객체가 될 뿐이다(tests/integration/test_mcp_scans_ps.py가 이 PC의 명령 정보로 확인한다)
$hqScriptBlockCommands = New-HqSet $hqRules.script_block_commands
# 인자를 모두 글자 그대로 적어야 하는 명령(WMI 클래스와 쿼리를 받는 Get-CimInstance)
$hqLiteralArgumentCommands = New-HqSet $hqRules.literal_argument_commands
# ForEach-Object를 메서드 호출 형태(PropertyAndMethodSet)로 바꾸는 매개변수와 그 별칭. ArgumentList의 별칭 Args는
# 2026-11-11 이 PC의 명령 정보에서 보았다. -Args를 주면 스크립트 블록의 글자가 MemberName이 된다
$hqForEachMemberParams = [string[]]@('MemberName', 'ArgumentList', 'Args')

$hqReasons = [System.Collections.Generic.List[object]]::new()
$hqSeen = [System.Collections.Generic.HashSet[object]]::new()  # 부모의 규칙이 이미 본 자식(리디렉션의 $null)
$hqPathSeen = [System.Collections.Generic.HashSet[object]]::new()  # 경로 규칙으로 이미 본 문자열(이유를 한 번만 쓴다)

$hqDefinitionMessage = '함수, 클래스, using, #requires, param, begin/process, dynamicparam, trap, data, configuration ' +
    '같은 정의는 쓸 수 없습니다.'

function Add-HqReason([string]$code, [string]$message, $node) {
    $hqReasons.Add([ordered]@{ code = $code; message = $message; line = $node.Extent.StartLineNumber })
}

function Get-HqAsciiCopy([string]$text) {
    # 출력 가능한 ASCII(U+0020~U+007E) 밖의 글자를 모두 지운 사본. 이름 사이에 끼운 보이지 않는 글자를 걸러 낸다
    return [regex]::Replace($text, '[^\x20-\x7E]', '')
}

function Test-HqPlainName([string]$name) {
    # 이름이 출력 가능한 ASCII(U+0020~U+007E)만으로 되어 있다. 변수, 멤버, 형식 이름은 목록과 견주기 전에 이것을 본다.
    # 그래야 이 PC, 검사, 서버의 비교 규칙이 달라도 이름이 같은 뜻이다
    return [regex]::IsMatch($name, '\A[\x20-\x7E]*\z')
}

function Test-HqPrefix([string]$name, [string[]]$names) {
    # PowerShell은 매개변수 이름과 별칭을 앞부분만 적어도 받는다. name이 names 중 어느 것의 앞부분이면 참.
    # name은 PARAM_NAME 규칙을 지난 영문, 숫자, 밑줄뿐이다. 그런 글자에서는 서수 비교가 바인더의 언어 비교와 같다
    # (2026-11-11 이 PC에서 후보 이름의 앞부분 251,748가지로 확인)
    foreach ($candidate in $names) {
        if ($candidate.StartsWith($name, [StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}

function Test-HqScriptBlock($block) {
    if ($null -ne $block.ParamBlock -or $null -ne $block.BeginBlock -or $null -ne $block.ProcessBlock -or
        $null -ne $block.DynamicParamBlock -or $null -ne $block.ScriptRequirements -or
        ($null -ne $block.UsingStatements -and $block.UsingStatements.Count -gt 0)) {
        Add-HqReason 'DEFINITION' $hqDefinitionMessage $block
    }
}

function Test-HqTraps($block) {
    if ($null -ne $block.Traps -and $block.Traps.Count -gt 0) {
        Add-HqReason 'DEFINITION' $hqDefinitionMessage $block
    }
}

function Test-HqWmiClass($string) {
    # 문자열이 스크립트의 어디에 있든 이름이 들어 있으면 거절한다. 보이지 않는 글자를 지운 사본에도 견준다
    $value = $string.Value
    $ascii = Get-HqAsciiCopy $value
    foreach ($class in $hqWmiClasses) {
        if ($value.IndexOf($class, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
            $ascii.IndexOf($class, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            Add-HqReason 'WMI_CLASS' "조회만 해도 서버를 바꾸거나 다른 컴퓨터에 닿는 WMI 클래스입니다($class)." $string
        }
    }
}

function Test-HqMergingRedirection($redirection) {
    # 2>&1만 받는다. 열거형 값끼리 견준다
    $stream = [System.Management.Automation.Language.RedirectionStream]
    if (-not ($redirection.FromStream -eq $stream::Error -and $redirection.ToStream -eq $stream::Output)) {
        Add-HqReason 'REDIRECT' '스트림 합치기는 2>&1만 받습니다.' $redirection
    }
}

function Test-HqFileRedirection($redirection) {
    # 파일로 가는 리디렉션은 거절하고 $null로 버리기(2>$null, >$null)만 받는다. 그 $null은 여기서 보았다고 표시한다.
    # 이름은 서수로 견준다. -eq는 ${nu<소프트 하이픈>ll}을 null과 같다고 보지만 실행 때는 평범한 변수다
    $location = $redirection.Location
    if ($location -is [System.Management.Automation.Language.VariableExpressionAst] -and -not $location.Splatted -and
        [string]::Equals($location.VariablePath.UserPath, 'null', [StringComparison]::OrdinalIgnoreCase)) {
        [void]$hqSeen.Add($location)
    } else {
        Add-HqReason 'REDIRECT' '파일로 가는 리디렉션은 쓸 수 없습니다. $null로 버리기(2>$null, >$null)만 받습니다.' $redirection
    }
}

function Test-HqParameterPlace($parameter) {
    # 매개변수의 규칙은 부모 명령의 규칙(Test-HqCommand)이 본다. 명령 밖에 있는 매개변수는 모르는 모양이다
    if ($parameter.Parent -isnot [System.Management.Automation.Language.CommandAst]) {
        Add-HqReason 'NODE' '검사가 받지 않는 문법입니다(명령 밖의 매개변수).' $parameter
    }
}

function Test-HqVariableValue($value) {
    # -OutVariable 같은 매개변수의 값: 문자열 상수이고, 맨 앞 +(덧붙이기)를 뗀 이름이 넣을 수 있는 변수 이름
    if ($value -isnot [System.Management.Automation.Language.StringConstantExpressionAst]) { return $false }
    $name = $value.Value
    if ($name.StartsWith('+', [StringComparison]::Ordinal)) { $name = $name.Substring(1) }
    return $name.IndexOf(':', [StringComparison]::Ordinal) -lt 0 -and -not $hqProtectedVariables.Contains($name)
}

function Test-HqBlockedPath([string]$value) {
    # \\, //로 시작하는 UNC 경로. PowerShell은 /도 경로 구분자로 받으므로 섞어 쓴 \/, /\도 같다
    if ([regex]::IsMatch($value, '\A[\\/]{2}')) { return $true }
    # 공급자 표기(FileSystem::\\z\x 등). 사용자별 자동 실행 조회에 흔한 Registry::만 받는다
    if ($value.IndexOf('::', [StringComparison]::Ordinal) -ge 0 -and
        -not $value.StartsWith('Registry::', [StringComparison]::OrdinalIgnoreCase)) { return $true }
    # 함수 본문을 스크립트 블록으로 꺼내는 드라이브(function:, alias:, variable:)
    foreach ($prefix in $hqPathPrefixes) {
        if ($value.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}

function Test-HqPathString($string) {
    # 문자열 하나에 경로 규칙을 쓴다. 보이지 않는 글자를 지운 사본에도 견준다. 이미 본 문자열은 다시 보지 않는다
    if (-not $hqPathSeen.Add($string)) { return }
    if ((Test-HqBlockedPath $string.Value) -or (Test-HqBlockedPath (Get-HqAsciiCopy $string.Value))) {
        Add-HqReason 'PATH' ('UNC 경로(\\, //), 공급자 표기(::, Registry::만 받음), function:, alias:, variable: ' +
            '경로는 명령 인자로 쓸 수 없습니다.') $string
    }
}

function Test-HqPaths($argument, [bool]$takesBlocks) {
    # 인자(붙은 값 포함) 안의 문자열 상수와 변수가 섞인 문자열. 괄호, 배열, $( ) 안도 본다. 다듬기 명령에 준 스크립트
    # 블록 안의 문자열은 인자가 아니라 코드이므로 보지 않는다(그 안의 명령은 따로 검사된다). 다른 명령의 인자 안에
    # 든 스크립트 블록은 안도 본다(Get-Content (1 | ForEach-Object { '\\z\x' })). FindAll의 둘째 인자가 그 구별이다
    $strings = $argument.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.StringConstantExpressionAst] -or
        $node -is [System.Management.Automation.Language.ExpandableStringExpressionAst]
    }, -not $takesBlocks)
    foreach ($string in $strings) { Test-HqPathString $string }
}

function Test-HqLiteralArgument($value) {
    # 글자 그대로 적은 인자: 문자열 상수(맨 글자, 변수 없는 따옴표 글자, 펼치지 않는 here-string), 숫자 상수, 또는
    # 그것들만으로 된 배열
    if ($value -is [System.Management.Automation.Language.ArrayLiteralAst]) {
        foreach ($item in $value.Elements) {
            if (-not (Test-HqLiteralArgument $item)) { return $false }
        }
        return $true
    }
    return $value -is [System.Management.Automation.Language.StringConstantExpressionAst] -or
        ($value -is [System.Management.Automation.Language.ConstantExpressionAst] -and $value.Value -is [ValueType])
}

function Add-HqLiteralArgumentReason($element, [string]$name) {
    Add-HqReason 'WMI_CLASS' ("$($name)의 인자는 모두 글자 그대로 적어야 합니다. 검사가 클래스 이름을 읽을 수 있어야 " +
        '합니다. 변수, 식, 변수가 든 문자열은 쓸 수 없습니다.') $element
}

function Resolve-HqCommandName($command) {
    # 이름을 글자 그대로 적은 명령의 이름(줄임말은 명령으로 바꾼다). & . 호출이거나 이름이 글자가 아니면 $null
    $first = $command.CommandElements[0]
    if ($command.InvocationOperator -ne [System.Management.Automation.Language.TokenKind]::Unknown -or
        $first -isnot [System.Management.Automation.Language.StringConstantExpressionAst] -or
        $first.StringConstantType -ne [System.Management.Automation.Language.StringConstantType]::BareWord) {
        return $null
    }
    $name = $first.Value
    if ($hqAliases.ContainsKey($name)) { $name = [string]$hqAliases[$name] }
    return $name
}

function Test-HqBlockPlace($block) {
    # 스크립트 블록을 적을 수 있는 자리: 다듬기 명령의 인자(요소 또는 매개변수에 붙은 값), 그 인자 안의 배열 항목과
    # 해시테이블 값(계산 속성. 사이에 괄호, $( ), @( ), 인덱스, 멤버, 연산자가 없다), switch의 조건. 다른 자리의 블록은
    # 다른 명령에 닿아 지연 바인딩될 수 있다. 지연 바인딩은 값의 형식으로 정해지므로 괄호, 배열, 변수를 거쳐 와도
    # 된다(2026-11-11 이 PC에서 Get-Date -Date로 측정)
    $parent = $block.Parent
    if ($parent -is [System.Management.Automation.Language.SwitchStatementAst]) {
        foreach ($clause in $parent.Clauses) {
            if ([object]::ReferenceEquals($clause.Item1, $block)) { return }
        }
    }
    # 계산 속성의 겹(배열 항목, 해시테이블 값)을 거슬러 올라간다
    $node = $block
    while ($true) {
        $parent = $node.Parent
        if ($parent -is [System.Management.Automation.Language.ArrayLiteralAst]) {
            $node = $parent
            continue
        }
        # 해시테이블 값은 문장이다: 해시테이블 > 파이프라인(요소 하나) > 식 문장 > 블록
        $pipeline = $null
        if ($parent -is [System.Management.Automation.Language.CommandExpressionAst] -and
            $parent.Redirections.Count -eq 0) {
            $pipeline = $parent.Parent
        }
        if ($pipeline -is [System.Management.Automation.Language.PipelineAst] -and
            $pipeline.PipelineElements.Count -eq 1 -and
            $pipeline.Parent -is [System.Management.Automation.Language.HashtableAst]) {
            $isValue = $false
            foreach ($pair in $pipeline.Parent.KeyValuePairs) {
                if ([object]::ReferenceEquals($pair.Item2, $pipeline)) { $isValue = $true }
            }
            if ($isValue) {
                $node = $pipeline.Parent
                continue
            }
        }
        break
    }
    $parent = $node.Parent
    $command = $null
    if ($parent -is [System.Management.Automation.Language.CommandParameterAst] -and
        [object]::ReferenceEquals($parent.Argument, $node)) {
        $command = $parent.Parent
    } elseif ($parent -is [System.Management.Automation.Language.CommandAst] -and
        -not [object]::ReferenceEquals($parent.CommandElements[0], $node)) {
        $command = $parent
    }
    if ($command -is [System.Management.Automation.Language.CommandAst]) {
        $name = Resolve-HqCommandName $command
        if ($null -ne $name -and $hqScriptBlockCommands.Contains($name)) { return }
    }
    Add-HqReason 'SCRIPT_BLOCK_ARG' ('스크립트 블록은 다듬기 명령(Where-Object, ForEach-Object, Select-Object, ' +
        'Sort-Object 등)의 인자와 그 계산 속성, switch의 조건 자리에만 적을 수 있습니다. 다른 자리의 블록은 다른 ' +
        '명령에 닿아 파이프라인 입력마다 실행되고, 그 결과가 매개변수 값이 될 수 있습니다.') $block
}

function Test-HqHashtable($table) {
    # 키는 글자 그대로 적은 문자열이나 숫자. PSTypeName 키는 개체에 다른 타입의 이름을 붙여, 속성을 읽을 때 그 타입에
    # 딸린 코드(types.ps1xml의 속성)가 돌게 한다(2026-11-11 검토자 측정)
    foreach ($pair in $table.KeyValuePairs) {
        $key = $pair.Item1
        $plain = $key -is [System.Management.Automation.Language.StringConstantExpressionAst] -or
            ($key -is [System.Management.Automation.Language.ConstantExpressionAst] -and $key.Value -is [ValueType])
        if (-not $plain) {
            Add-HqReason 'HASH_KEY' '해시테이블의 키는 글자 그대로 적어야 합니다. 변수, 식, 변수가 든 문자열은 쓸 수 없습니다.' $key
            continue
        }
        $text = [string]$key.Value
        if ([string]::Equals($text, 'PSTypeName', [StringComparison]::OrdinalIgnoreCase) -or
            [string]::Equals((Get-HqAsciiCopy $text), 'PSTypeName', [StringComparison]::OrdinalIgnoreCase)) {
            Add-HqReason 'TYPE' ('PSTypeName 키는 쓸 수 없습니다. 개체에 다른 타입의 동작을 붙여, 속성을 읽을 때 그 ' +
                '타입의 코드가 돕니다.') $key
        }
    }
}

function Test-HqExternalArgs($allowed, [string[]]$texts) {
    if ($allowed -isnot [array]) {
        # netstat: 요소마다 -와 허용된 글자들(-ano, -a -n 등). 대소문자를 구별하는 정규식에 두 경우를 모두 적는다
        $letters = [string]$allowed.flag_letters
        $pattern = '\A-[' + $letters.ToLowerInvariant() + $letters.ToUpperInvariant() + ']+\z'
        foreach ($text in $texts) {
            if (-not [regex]::IsMatch($text, $pattern)) { return $false }
        }
        return $true
    }
    # 나머지: 인자 목록이 허용된 목록 중 하나와 같다
    foreach ($list in $allowed) {
        $expected = [string[]]@($list)
        if ($expected.Count -ne $texts.Count) { continue }
        $same = $true
        for ($i = 0; $i -lt $texts.Count; $i++) {
            if (-not [string]::Equals($expected[$i], $texts[$i], [StringComparison]::OrdinalIgnoreCase)) { $same = $false }
        }
        if ($same) { return $true }
    }
    return $false
}

function Test-HqCommand($command) {
    $elements = $command.CommandElements
    $first = $elements[0]
    # 1. &, . 호출과 이름이 글자 그대로가 아닌 명령(변수, 식, 따옴표)
    if ($command.InvocationOperator -ne [System.Management.Automation.Language.TokenKind]::Unknown -or
        $first -isnot [System.Management.Automation.Language.StringConstantExpressionAst] -or
        $first.StringConstantType -ne [System.Management.Automation.Language.StringConstantType]::BareWord) {
        Add-HqReason 'INVOKE' ('& 와 . 호출, 변수나 식이나 따옴표로 정한 명령은 쓸 수 없습니다. 허용 명령의 이름을 ' +
            '글자 그대로 적으세요.') $command
        return
    }
    $name = $first.Value
    # 2. 외부 명령은 인자까지 고정이다. 아래 매개변수 규칙은 보지 않는다(netstat의 -s, -o는 Session, OutVariable의
    # 앞부분이지만 외부 명령에는 PowerShell 매개변수가 아니다)
    if ($hqExternal.ContainsKey($name)) {
        $texts = [string[]]@(for ($i = 1; $i -lt $elements.Count; $i++) { $elements[$i].Extent.Text })
        if (-not (Test-HqExternalArgs $hqExternal[$name] $texts)) {
            Add-HqReason 'EXTERNAL_ARGS' "외부 명령 $($name)의 인자가 허용된 모양이 아닙니다." $command
        }
        return
    }
    # 3. 줄임말은 명령으로 바꿔 허용 목록과 견준다
    if ($hqAliases.ContainsKey($name)) { $name = [string]$hqAliases[$name] }
    if (-not $hqCommands.Contains($name)) {
        Add-HqReason 'COMMAND' "허용 목록에 없는 명령입니다($name)." $command
        return
    }
    # 원격 매개변수가 파이프라인 입력으로도 묶이는 명령은 파이프라인의 맨 앞에만 쓴다. 앞 명령이 낸 개체의 속성
    # (Get-SmbConnection의 ServerName 등)이 그 매개변수로 묶여 다른 컴퓨터로 갈 수 있다
    if ($hqPipelineRemote.Contains($name)) {
        $pipeline = $command.Parent
        $starts = $pipeline -is [System.Management.Automation.Language.PipelineAst] -and
            [object]::ReferenceEquals($pipeline.PipelineElements[0], $command)
        if (-not $starts) {
            Add-HqReason 'PIPELINE_INPUT' ("$($name)은(는) 파이프라인의 맨 앞에만 쓸 수 있습니다. 앞 명령이 낸 개체의 " +
                '속성이 다른 컴퓨터 이름으로 묶일 수 있습니다.') $command
        }
    }
    $remote = $hqRemoteParams
    if ($hqRemoteAliases.ContainsKey($name)) { $remote = $remote + [string[]]@($hqRemoteAliases[$name]) }
    $isForEach = [string]::Equals($name, 'ForEach-Object', [StringComparison]::OrdinalIgnoreCase)
    $takesBlocks = $hqScriptBlockCommands.Contains($name)
    # WMI 클래스나 쿼리를 받는 명령(Get-CimInstance)은 인자를 모두 글자 그대로 적는다. 이어 붙여 만든 클래스 이름은
    # WMI_CLASS 규칙이 읽을 수 없다
    $literalOnly = $hqLiteralArgumentCommands.Contains($name)
    for ($i = 1; $i -lt $elements.Count; $i++) {
        $element = $elements[$i]
        if ($element -is [System.Management.Automation.Language.CommandParameterAst]) {
            $parameter = $element.ParameterName
            $value = $element.Argument  # 붙은 값(-Path:x). 없으면 $null
            # 매개변수 이름은 영문, 숫자, 밑줄만. 바인더는 이름을 언어 규칙으로 견주어 보이지 않는 글자를 무시하고
            # ß를 ss로 보므로, 그런 글자를 끼운 이름도 원래 매개변수로 묶인다(2026-11-11 측정)
            if (-not [regex]::IsMatch($parameter, '\A[A-Za-z0-9_]+\z')) {
                Add-HqReason 'PARAM_NAME' ('매개변수 이름에 영문, 숫자, 밑줄이 아닌 글자가 있습니다. 보이지 않는 글자도 ' +
                    '걸립니다.') $element
                continue
            }
            # 4. 다른 컴퓨터로 가는 매개변수와 그 별칭. 앞부분 줄임도 같다
            if (Test-HqPrefix $parameter $remote) {
                Add-HqReason 'PARAM_REMOTE' "다른 컴퓨터로 가는 매개변수는 쓸 수 없습니다(-$parameter)." $element
            }
            # 5. 변수에 값을 넣는 매개변수의 값(붙은 값 또는 다음 요소)
            if (Test-HqPrefix $parameter $hqVariableParams) {
                $target = $value
                if ($null -eq $target -and $i + 1 -lt $elements.Count) { $target = $elements[$i + 1] }
                if (-not (Test-HqVariableValue $target)) {
                    Add-HqReason 'PARAM_VARIABLE' ("-$($parameter)의 값은 평범한 변수 이름을 글자로 적어야 합니다. " +
                        'PowerShell이 쓰는 변수와 범위나 드라이브가 붙은 이름은 안 됩니다.') $element
                }
            }
            # 6. ForEach-Object는 스크립트 블록 형태만. 붙은 값도 스크립트 블록이어야 한다(InputObject는 빼고)
            if ($isForEach) {
                $memberForm = Test-HqPrefix $parameter $hqForEachMemberParams
                $notBlock = $null -ne $value -and
                    $value -isnot [System.Management.Automation.Language.ScriptBlockExpressionAst] -and
                    -not (Test-HqPrefix $parameter @('InputObject'))
                if ($memberForm -or $notBlock) { Add-HqForEachReason $element }
            }
            # 7. 붙은 값의 경로
            if ($null -ne $value) { Test-HqPaths $value $takesBlocks }
            # 9. 글자 그대로만 받는 명령의 붙은 값
            if ($null -ne $value -and $literalOnly -and -not (Test-HqLiteralArgument $value)) {
                Add-HqLiteralArgumentReason $value $name
            }
            continue
        }
        # 6. ForEach-Object의 매개변수가 아닌 요소는 스크립트 블록이어야 한다. 값이 붙지 않은 -InputObject의 값만 예외
        if ($isForEach -and $element -isnot [System.Management.Automation.Language.ScriptBlockExpressionAst]) {
            $previous = $elements[$i - 1]
            $afterInputObject = $previous -is [System.Management.Automation.Language.CommandParameterAst] -and
                $null -eq $previous.Argument -and (Test-HqPrefix $previous.ParameterName @('InputObject'))
            if (-not $afterInputObject) { Add-HqForEachReason $element }
        }
        # 8. 스플래팅(@p)은 변수의 규칙(Test-HqVariable)이 거절한다
        # 7. 인자의 경로
        Test-HqPaths $element $takesBlocks
        # 9. 글자 그대로만 받는 명령의 인자
        if ($literalOnly -and -not (Test-HqLiteralArgument $element)) { Add-HqLiteralArgumentReason $element $name }
        # 스크립트 블록 인자를 받는 명령인지는 블록 쪽의 규칙(Test-HqBlockPlace)이 본다
    }
}

function Add-HqForEachReason($element) {
    Add-HqReason 'FOREACH_FORM' ('ForEach-Object(%)는 스크립트 블록 형태({ ... })만 받습니다. 메서드 이름, ' +
        '-MemberName, -ArgumentList는 쓸 수 없습니다.') $element
}

function Test-HqVariable($variable) {
    # 변수 읽기: 스플래팅이 아니고, 드라이브 형식이면 드라이브가 env이고, 범위 표시를 뗀 이름이 읽으면 안 되는 변수가
    # 아니다. $using:x는 UsingExpressionAst가 감싸고 안쪽 변수의 이름에는 using:이 없다
    if ($variable.Splatted) {
        Add-HqReason 'SPLAT' '스플래팅(@변수)은 쓸 수 없습니다.' $variable
        return
    }
    $path = $variable.VariablePath
    $name = $path.UserPath
    if (-not (Test-HqPlainName $name)) {
        Add-HqReason 'VARIABLE' '변수 이름에 출력 가능한 ASCII가 아닌 글자가 있습니다. 보이지 않는 글자도 걸립니다.' $variable
        return
    }
    if ($path.IsDriveQualified) {
        if (-not [string]::Equals($path.DriveName, 'env', [StringComparison]::OrdinalIgnoreCase)) {
            Add-HqReason 'VARIABLE' ('드라이브 형식의 변수는 $env:만 쓸 수 있습니다. ${C:\x}, $function:x 같은 모양은 ' +
                '파일과 함수 본문을 읽습니다.') $variable
        }
        return
    }
    # 범위 표시(global:, script:, local:, private:, variable:)를 뗀 이름. 콜론이 여럿이면 마지막 콜론 뒤다
    $bare = $name.Substring($name.LastIndexOf(':', [StringComparison]::Ordinal) + 1)
    if ($hqBlockedReads.Contains($bare)) {
        Add-HqReason 'VARIABLE' "읽을 수 없는 변수입니다(`$$bare). 명령을 실행하거나 화면을 다루는 길이 됩니다." $variable
    }
}

function Get-HqTypeProblem($typeName) {
    # 형식 이름이 허용 목록(types)에 없으면 그 까닭을, 있으면 $null을 돌려준다. 배열 표기([string[]])는 요소 형식으로
    # 본다. 제네릭과 어셈블리를 적은 이름([int, mscorlib])은 받지 않는다
    while ($typeName -is [System.Management.Automation.Language.ArrayTypeName]) { $typeName = $typeName.ElementType }
    if ($typeName -isnot [System.Management.Automation.Language.TypeName]) { return '제네릭 형식은 쓸 수 없습니다.' }
    $name = $typeName.FullName
    if (-not (Test-HqPlainName $name)) { return '형식 이름에 출력 가능한 ASCII가 아닌 글자가 있습니다.' }
    if (-not $hqTypes.Contains($name)) { return "허용 목록에 없는 형식입니다($name)." }
    return $null
}

function Test-HqType($node, $typeName) {
    $problem = Get-HqTypeProblem $typeName
    if ($null -ne $problem) { Add-HqReason 'TYPE' $problem $node }
}

function Test-HqTypeConstraint($constraint) {
    # 변환([int]$x)의 형식도 이것으로 본다. catch [형식]의 형식은 잡을 예외를 고를 뿐이므로 목록과 견주지 않지만,
    # 점으로 이은 영문, 숫자, 밑줄 이름만 받는다. 어셈블리를 적은 이름은 파서가 그 어셈블리를 찾게 한다(검토자 측정)
    if ($constraint.Parent -isnot [System.Management.Automation.Language.CatchClauseAst]) {
        Test-HqType $constraint $constraint.TypeName
    } elseif (-not [regex]::IsMatch($constraint.TypeName.FullName, '\A[A-Za-z0-9_.]+\z')) {
        Add-HqReason 'TYPE' ('catch의 형식은 점으로 이은 이름([System.IO.IOException])만 쓸 수 있습니다. 어셈블리, ' +
            '제네릭, 배열 표기는 안 됩니다.') $constraint
    }
}

function Test-HqAssignable($target) {
    # 넣을 수 있는 자리: 범위와 드라이브 표시가 없고 스플래팅이 아닌 변수로, 이름이 넣을 수 있는 변수 이름인 것.
    # 또는 그런 변수를 감싼 허용 형식의 변환([int]$x = 5). 왼쪽이 여럿이거나 속성, 인덱스면 거짓이다.
    # $variable:x는 파서가 드라이브로 보지 않지만 범위 표시가 있으므로(IsUnqualified가 거짓) 받지 않는다
    if ($target -is [System.Management.Automation.Language.ConvertExpressionAst]) {
        if ($null -ne (Get-HqTypeProblem $target.Type.TypeName)) { return $false }
        $target = $target.Child
    }
    if ($target -isnot [System.Management.Automation.Language.VariableExpressionAst] -or $target.Splatted -or
        -not $target.VariablePath.IsUnqualified) {
        return $false
    }
    $name = $target.VariablePath.UserPath
    return (Test-HqPlainName $name) -and $name.IndexOf(':', [StringComparison]::Ordinal) -lt 0 -and
        -not $hqProtectedVariables.Contains($name)
}

function Add-HqAssignReason($node) {
    Add-HqReason 'ASSIGN' ('값은 평범한 지역 변수에만 넣을 수 있습니다. 속성, 인덱스, 여러 변수, 범위나 드라이브가 붙은 ' +
        '변수, PowerShell이 쓰는 변수($PSDefaultParameterValues 등)에는 넣을 수 없습니다.') $node
}

function Test-HqAssignment($assignment) {
    # 대입(=, += 등)의 왼쪽
    if (-not (Test-HqAssignable $assignment.Left)) { Add-HqAssignReason $assignment }
}

function Test-HqUnary($unary) {
    # ++, --(앞뒤 넷)는 대상에 값을 넣는다. 다른 단항 연산자(-not, !, -, -split 등)는 받는다
    $kind = [System.Management.Automation.Language.TokenKind]
    $op = $unary.TokenKind
    $increments = $op -eq $kind::PlusPlus -or $op -eq $kind::MinusMinus -or $op -eq $kind::PostfixPlusPlus -or
        $op -eq $kind::PostfixMinusMinus
    if ($increments -and -not (Test-HqAssignable $unary.Child)) { Add-HqAssignReason $unary }
}

function Test-HqForEach($loop) {
    # foreach의 반복 변수도 값을 넣는 자리다. Flags(-Parallel)는 없어야 한다
    if ($loop.Flags -ne [System.Management.Automation.Language.ForEachFlags]::None -or
        -not (Test-HqAssignable $loop.Variable)) {
        Add-HqAssignReason $loop
    }
}

function Test-HqBinary($binary) {
    # -as의 오른쪽은 허용 형식을 글자로 적은 것. 문자열이나 변수면 PowerShell이 아무 형식의 생성자나 부른다
    if ($binary.Operator -ne [System.Management.Automation.Language.TokenKind]::As) { return }
    $right = $binary.Right
    if ($right -isnot [System.Management.Automation.Language.TypeExpressionAst] -or
        $null -ne (Get-HqTypeProblem $right.TypeName)) {
        Add-HqReason 'AS' ('-as의 오른쪽은 허용 형식을 글자로 적은 것([int])이어야 합니다. 문자열이나 변수로 정한 ' +
            '형식은 그 형식의 생성자를 부릅니다.') $binary
    }
}

function Test-HqMemberName($member) {
    # 글자로 적은 멤버(속성, 메서드) 이름은 출력 가능한 ASCII만 받는다
    $name = $member.Member
    if ($name -is [System.Management.Automation.Language.StringConstantExpressionAst] -and
        -not (Test-HqPlainName $name.Value)) {
        Add-HqReason 'METHOD' '멤버 이름에 출력 가능한 ASCII가 아닌 글자가 있습니다. 보이지 않는 글자도 걸립니다.' $member
        return $false
    }
    return $true
}

function Test-HqStaticTarget($member) {
    # 정적 멤버(::)는 형식을 글자로 적은 것에서만 쓴다. 그 형식이 허용인지는 TypeExpressionAst의 규칙이 본다
    if ($member.Expression -is [System.Management.Automation.Language.TypeExpressionAst]) { return $true }
    Add-HqReason 'TYPE' ('정적 멤버(::)는 허용 형식을 글자로 적은 것([math]::Round)에서만 쓸 수 있습니다. 변수로 ' +
        '정한 형식은 안 됩니다.') $member
    return $false
}

function Test-HqMember($member) {
    # 속성 읽기. 정적이 아니면 이름만 본다
    [void](Test-HqMemberName $member)
    if ($member.Static) { [void](Test-HqStaticTarget $member) }
}

function Test-HqInvokeMember($invoke) {
    # 메서드 호출. 이름은 문자열 상수다. 정적이면 형식을 글자로 적었고 이름이 static_methods[형식]에 있다. 아니면
    # 이름이 instance_methods에 있다. 정적 메서드는 배열 표기 없는 형식 이름 그대로 찾는다
    $name = $invoke.Member
    if ($name -isnot [System.Management.Automation.Language.StringConstantExpressionAst]) {
        Add-HqReason 'METHOD' '메서드 이름은 글자 그대로 적어야 합니다. 변수나 식으로 정한 이름은 쓸 수 없습니다.' $invoke
        return
    }
    if (-not (Test-HqMemberName $invoke)) { return }
    $method = $name.Value
    if ($invoke.Static) {
        if (-not (Test-HqStaticTarget $invoke)) { return }
        $typeName = $invoke.Expression.TypeName
        $allowed = $null
        if ($typeName -is [System.Management.Automation.Language.TypeName] -and
            $hqStaticMethods.ContainsKey($typeName.FullName)) {
            $allowed = $hqStaticMethods[$typeName.FullName]
        }
        if ($null -eq $allowed -or -not $allowed.Contains($method)) {
            Add-HqReason 'METHOD' "허용 목록에 없는 정적 메서드입니다($method)." $invoke
        }
    } elseif (-not $hqInstanceMethods.Contains($method)) {
        Add-HqReason 'METHOD' ("허용 목록에 없는 메서드입니다($method). 글자 처리는 -f, -replace, -split, -join " +
            '연산자로 하세요.') $invoke
    }
}

function Test-HqSwitch($switch) {
    # switch -File은 명령 인자가 아니어서 경로 규칙을 거치지 않는다
    if ($switch.Flags.HasFlag([System.Management.Automation.Language.SwitchFlags]::File)) {
        Add-HqReason 'SWITCH_FILE' 'switch -File은 쓸 수 없습니다. 파일은 Get-Content로 읽으세요.' $switch
    }
}

$hqDefinition = { param($node) Add-HqReason 'DEFINITION' $hqDefinitionMessage $node }
# 받는 트리 종류. 값이 $null이면 그대로 받고, 스크립트 블록이면 그 규칙을 따른다. 여기 없는 종류는 NODE다.
# 열쇠는 노드의 .NET 형식 이름이고 서수로 찾는다
$hqKinds = [System.Collections.Generic.Dictionary[string, scriptblock]]::new([StringComparer]::Ordinal)
$hqKinds.Add('ScriptBlockAst', { param($node) Test-HqScriptBlock $node })
$hqKinds.Add('NamedBlockAst', { param($node) Test-HqTraps $node })
$hqKinds.Add('StatementBlockAst', { param($node) Test-HqTraps $node })
foreach ($hqKind in 'FunctionDefinitionAst', 'TypeDefinitionAst', 'UsingStatementAst', 'TrapStatementAst',
    'DataStatementAst', 'ParamBlockAst', 'ConfigurationDefinitionAst', 'DynamicKeywordStatementAst') {
    $hqKinds.Add($hqKind, $hqDefinition)
}
foreach ($hqKind in 'PipelineAst', 'CommandExpressionAst', 'ConstantExpressionAst', 'ArrayLiteralAst') {
    $hqKinds.Add($hqKind, $null)
}
# 제어 흐름과 식을 묶는 종류. 안쪽의 명령, 변수, 문자열은 저마다의 종류로 따로 검사된다. 스크립트 블록 안도 바깥과
# 같은 규칙을 따른다. ConvertExpressionAst의 형식은 자식 TypeConstraintAst의 규칙이 본다
foreach ($hqKind in 'IfStatementAst', 'WhileStatementAst', 'DoWhileStatementAst', 'DoUntilStatementAst',
    'ForStatementAst', 'TryStatementAst', 'CatchClauseAst', 'ThrowStatementAst', 'ReturnStatementAst',
    'BreakStatementAst', 'ContinueStatementAst', 'ExitStatementAst', 'ParenExpressionAst', 'SubExpressionAst',
    'ArrayExpressionAst', 'IndexExpressionAst', 'ConvertExpressionAst') {
    $hqKinds.Add($hqKind, $null)
}
$hqKinds.Add('HashtableAst', { param($node) Test-HqHashtable $node })
$hqKinds.Add('ScriptBlockExpressionAst', { param($node) Test-HqBlockPlace $node })
$hqKinds.Add('StringConstantExpressionAst', { param($node) Test-HqWmiClass $node })
$hqKinds.Add('ExpandableStringExpressionAst', { param($node) Test-HqWmiClass $node })
$hqKinds.Add('MergingRedirectionAst', { param($node) Test-HqMergingRedirection $node })
$hqKinds.Add('FileRedirectionAst', { param($node) Test-HqFileRedirection $node })
$hqKinds.Add('CommandAst', { param($node) Test-HqCommand $node })
$hqKinds.Add('CommandParameterAst', { param($node) Test-HqParameterPlace $node })
$hqKinds.Add('VariableExpressionAst', { param($node) Test-HqVariable $node })
$hqKinds.Add('UsingExpressionAst', {
    param($node)
    Add-HqReason 'USING' '$using:은 쓸 수 없습니다. 이 PC의 변수를 서버로 가져가는 문법입니다.' $node
})
$hqKinds.Add('AssignmentStatementAst', { param($node) Test-HqAssignment $node })
$hqKinds.Add('UnaryExpressionAst', { param($node) Test-HqUnary $node })
$hqKinds.Add('ForEachStatementAst', { param($node) Test-HqForEach $node })
$hqKinds.Add('BinaryExpressionAst', { param($node) Test-HqBinary $node })
$hqKinds.Add('TypeExpressionAst', { param($node) Test-HqType $node $node.TypeName })
$hqKinds.Add('TypeConstraintAst', { param($node) Test-HqTypeConstraint $node })
$hqKinds.Add('MemberExpressionAst', { param($node) Test-HqMember $node })
$hqKinds.Add('InvokeMemberExpressionAst', { param($node) Test-HqInvokeMember $node })
$hqKinds.Add('SwitchStatementAst', { param($node) Test-HqSwitch $node })

$hqTokens = $null
$hqErrors = $null
$hqTree = [System.Management.Automation.Language.Parser]::ParseInput(
    [string]$hqInput.script, [ref]$hqTokens, [ref]$hqErrors)
if ($hqErrors.Count -gt 0) {
    foreach ($hqError in $hqErrors) {
        $hqReasons.Add([ordered]@{ code = 'PARSE_ERROR'; message = $hqError.Message; line = $hqError.Extent.StartLineNumber })
    }
} else {
    # 모든 노드를 한 번씩 본다(부모가 자식보다 먼저 온다). 중첩된 스크립트 블록 안도 본다
    foreach ($hqNode in $hqTree.FindAll({ $true }, $true)) {
        if ($hqSeen.Contains($hqNode)) { continue }
        $hqKind = $hqNode.GetType().Name
        if (-not $hqKinds.ContainsKey($hqKind)) {
            Add-HqReason 'NODE' "검사가 받지 않는 문법입니다($hqKind)." $hqNode
            continue
        }
        $hqRule = $hqKinds[$hqKind]
        if ($null -ne $hqRule) { & $hqRule $hqNode }
    }
}

$hqResult = [ordered]@{ ok = ($hqReasons.Count -eq 0); reasons = $hqReasons }
[Console]::Out.WriteLine((ConvertTo-Json -InputObject $hqResult -Depth 4 -Compress))
[Environment]::Exit(0)
