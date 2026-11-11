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
#
# 이유 코드: PARSE_ERROR NODE DEFINITION INVOKE COMMAND EXTERNAL_ARGS PIPELINE_INPUT PARAM_NAME PARAM_REMOTE
# PARAM_VARIABLE FOREACH_FORM PATH SPLAT REDIRECT WMI_CLASS. Python이 PARSER_HAZARD와 CHECK_FAILED를 더한다.
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
# ForEach-Object를 메서드 호출 형태(PropertyAndMethodSet)로 바꾸는 매개변수와 그 별칭. ArgumentList의 별칭 Args는
# 2026-11-11 이 PC의 명령 정보에서 보았다. -Args를 주면 스크립트 블록의 글자가 MemberName이 된다
$hqForEachMemberParams = [string[]]@('MemberName', 'ArgumentList', 'Args')

$hqReasons = [System.Collections.Generic.List[object]]::new()
$hqSeen = [System.Collections.Generic.HashSet[object]]::new()  # 부모의 규칙이 이미 본 자식(리디렉션의 $null)

$hqDefinitionMessage = '함수, 클래스, using, #requires, param, begin/process, dynamicparam, trap, data, configuration ' +
    '같은 정의는 쓸 수 없습니다.'

function Add-HqReason([string]$code, [string]$message, $node) {
    $hqReasons.Add([ordered]@{ code = $code; message = $message; line = $node.Extent.StartLineNumber })
}

function Get-HqAsciiCopy([string]$text) {
    # 출력 가능한 ASCII(U+0020~U+007E) 밖의 글자를 모두 지운 사본. 이름 사이에 끼운 보이지 않는 글자를 걸러 낸다
    return [regex]::Replace($text, '[^\x20-\x7E]', '')
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

function Test-HqPaths($argument) {
    # 인자(붙은 값 포함) 안의 문자열 상수와 변수가 섞인 문자열. 안쪽 스크립트 블록은 그 안의 명령이 따로 검사되므로
    # 빼고 본다(FindAll의 둘째 인자 $false). 보이지 않는 글자를 지운 사본에도 견준다
    $strings = $argument.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.StringConstantExpressionAst] -or
        $node -is [System.Management.Automation.Language.ExpandableStringExpressionAst]
    }, $false)
    foreach ($string in $strings) {
        if ((Test-HqBlockedPath $string.Value) -or (Test-HqBlockedPath (Get-HqAsciiCopy $string.Value))) {
            Add-HqReason 'PATH' ('UNC 경로(\\, //), 공급자 표기(::, Registry::만 받음), function:, alias:, variable: ' +
                '경로는 인자로 쓸 수 없습니다.') $string
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
            if ($null -ne $value) { Test-HqPaths $value }
            continue
        }
        # 6. ForEach-Object의 매개변수가 아닌 요소는 스크립트 블록이어야 한다. 값이 붙지 않은 -InputObject의 값만 예외
        if ($isForEach -and $element -isnot [System.Management.Automation.Language.ScriptBlockExpressionAst]) {
            $previous = $elements[$i - 1]
            $afterInputObject = $previous -is [System.Management.Automation.Language.CommandParameterAst] -and
                $null -eq $previous.Argument -and (Test-HqPrefix $previous.ParameterName @('InputObject'))
            if (-not $afterInputObject) { Add-HqForEachReason $element }
        }
        # 8. 스플래팅
        if ($element -is [System.Management.Automation.Language.VariableExpressionAst] -and $element.Splatted) {
            Add-HqReason 'SPLAT' '스플래팅(@변수)은 쓸 수 없습니다.' $element
        }
        # 7. 인자의 경로
        Test-HqPaths $element
    }
}

function Add-HqForEachReason($element) {
    Add-HqReason 'FOREACH_FORM' ('ForEach-Object(%)는 스크립트 블록 형태({ ... })만 받습니다. 메서드 이름, ' +
        '-MemberName, -ArgumentList는 쓸 수 없습니다.') $element
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
$hqKinds.Add('StringConstantExpressionAst', { param($node) Test-HqWmiClass $node })
$hqKinds.Add('ExpandableStringExpressionAst', { param($node) Test-HqWmiClass $node })
$hqKinds.Add('MergingRedirectionAst', { param($node) Test-HqMergingRedirection $node })
$hqKinds.Add('FileRedirectionAst', { param($node) Test-HqFileRedirection $node })
$hqKinds.Add('CommandAst', { param($node) Test-HqCommand $node })
$hqKinds.Add('CommandParameterAst', { param($node) Test-HqParameterPlace $node })

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
