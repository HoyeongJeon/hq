# hq 접속 스크립트. 탭의 powershell.exe(내 PC)에서 실행된 뒤 원격 세션으로 들어간다.
# 맨 앞의 prompt 함수가 안전장치다. 이 로컬 셸이 자기 프롬프트를 띄우려는 순간(원격 세션이
# 끝났거나 시작되지 못함) 프롬프트 대신 세션을 정리하고 프로세스를 끝낸다. PowerShell은
# 입력을 읽기 전에 프롬프트를 먼저 그리므로, 서버에 치려던 입력이 내 PC에서 실행될 틈이 없다.
function global:prompt {
    $code = 1
    try {
        $s = $global:__hq_session
        if ($null -ne $s) {
            if ("$($s.State)" -eq 'Opened') { $code = 0 } else { $code = 20 }
            Remove-PSSession -Session $s -ErrorAction SilentlyContinue
        }
    } catch { } finally { [Environment]::Exit($code) }
}

# 준비 신호: 안전장치를 건 바로 다음에 콘솔 제목을 바꾼다. ConPTY가 이를 화면에 보이지 않는 제목 시퀀스로
# 내보내고, hq는 이것을 본 뒤에야 탭의 입력을 보낸다. 백신이 본문을 막으면 신호가 오지 않아 hq가 탭을 끝낸다.
# FullLanguage가 아니면(예: 정책이 건 제한 언어 모드) 위의 [Environment]::Exit가 거부되어 안전장치가 듣지 않으므로
# 신호를 내지 않는다. 제목 바꾸기도 함께 거부되는 데 기대지 않고 직접 확인한다.
# 표시 글자를 조각으로 나눠 적는 이유: 이 줄이 실패하면 오류 메시지가 이 줄의 원문을 그대로 보여 주므로,
# 원문에 표시가 통째로 있으면 hq가 그 메시지를 신호로 착각한다.
if ($ExecutionContext.SessionState.LanguageMode -eq 'FullLanguage') { $Host.UI.RawUI.WindowTitle = 'hq:safety' + '-ready' }

# 세션 만들기 실패를 hq 종료 코드로 바꾼다.
# 10 로그인 거부, 11 응답 없음, 12 TrustedHosts 없음, 13 동시 세션 한도, 1 그 밖
# 한국어 Windows는 메시지를 한국어로 주므로 FullyQualifiedErrorId의 쉼표 앞부분으로 정한다(V3 실서버 표본,
# docs/verification.md). 1312는 Negotiate 인증으로 TrustedHosts에 없는 서버에 접속할 때 나온다.
# 메시지는 FQID로 정해지지 않을 때만 본다. 한도 초과만은 표본이 없어 FQID를 모르므로 메시지로 맨 먼저 본다.
function Get-HqExitCode {
    param($ErrorRecord)
    $kind = "$($ErrorRecord.FullyQualifiedErrorId)".Split(',')[0]
    $message = "$($ErrorRecord.Exception.Message)"
    if ($message -match 'concurrent shells|MaxShellsPerUser') { return 13 }
    if ($kind -eq 'AccessDenied') { return 10 }
    if (@('1312', 'ServerNotTrusted') -contains $kind) { return 12 }
    if (@('WinRMOperationTimeout', 'CannotConnect', 'ComputerNotFound') -contains $kind) { return 11 }
    if ($message -match 'TrustedHosts') { return 12 }
    return 1
}

#region main
$ErrorActionPreference = 'Stop'
# 매개변수는 ASCII만 쓴 JSON 작은따옴표 문자열로 들어온다(bootstrap.py의 ps_json_literal).
$p = __HQ_PARAMS__ | ConvertFrom-Json
Write-Host "$($p.host)에 접속 중..."
try {
    $secret = (Get-Content -LiteralPath $p.secret_path -Raw).Trim()
    $cred = New-Object System.Management.Automation.PSCredential($p.username, (ConvertTo-SecureString -String $secret))
} catch {
    Write-Host "저장된 비밀번호를 읽을 수 없습니다: $($_.Exception.Message)"
    [Environment]::Exit(14)
}
try {
    $global:__hq_session = New-PSSession -ComputerName $p.host -Credential $cred -Authentication Negotiate
} catch {
    Write-Host $_.Exception.Message
    [Environment]::Exit((Get-HqExitCode $_))
}
try {
    Invoke-Command -Session $global:__hq_session -ArgumentList $p.nickname -ScriptBlock {
        param($nick)
        $global:__hq_nick = $nick
        function global:prompt {
            "[$($global:__hq_nick)] PS $($executionContext.SessionState.Path.CurrentLocation)$('>' * ($nestedPromptLevel + 1)) "
        }
    }
} catch {
    Write-Host "프롬프트에 별명을 붙이지 못했습니다: $($_.Exception.Message)"
}
#region tab
# Tab: 회색 미리보기(PSReadLine이 입력 기록에서 고른 예측)가 보이면 받아들이고, 없으면 지금처럼 자동완성한다
# (TabCompleteNext라서 Tab을 거듭 누르면 후보가 차례로 바뀐다). PSReadLine은 Enter-PSSession 안에서도 이 PC의
# 셸에서 돌므로 원격 세션에 들어가기 전에 여기서 건다. 자동완성 후보는 PSReadLine이 원격 세션에 물어 온다.
# 미리보기가 보였는지는 AcceptSuggestion 앞뒤의 입력 줄을 견주어 안다(받을 미리보기가 없으면 줄이 그대로다).
# AcceptSuggestion은 PSReadLine 2.1부터 있다. PSReadLine이 없거나 그보다 낮으면 Tab을 그대로 두고 아무것도 찍지 않는다.
# 키를 걸지 못해도 탭을 끝내거나 접속을 건너뛰지 않고 한 줄로 알린다.
try {
    $readLine = 'Microsoft.PowerShell.PSConsoleReadLine' -as [type]
    if ($null -ne $readLine -and $null -ne $readLine.GetMethod('AcceptSuggestion')) {
        # 자동완성 순환 상태: 이 Tab이 TabCompleteNext로 채운 뒤의 커서와 입력 줄. 다음 Tab 때 줄과 커서가 그대로면
        # 순환 중이므로 미리보기가 보여도 받지 않고 다음 후보로 간다. 채운 줄로 시작하는 기록이 있으면 PSReadLine이
        # 그 뒤에 미리보기를 그리는데, 그것을 받으면 후보를 차례로 볼 수 없다. 다른 키로 줄이 바뀌면 순환이 끝나고
        # 다음 Tab은 다시 받기 아니면 자동완성이다. 키 처리기는 부를 때마다 새 범위에서 돌므로 이 PC 셸의 전역에 둔다.
        $global:__hq_tab_cycle = $null
        $tabKey = @{
            Chord = 'Tab'
            BriefDescription = 'AcceptSuggestionOrTabComplete'
            Description = '회색 미리보기가 있으면 받아들이고, 없으면 자동완성한다'
            ScriptBlock = {
                param($key, $arg)
                $line = $null
                $cursor = $null
                [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState([ref]$line, [ref]$cursor)
                $accepted = $false
                if ($global:__hq_tab_cycle -cne "$cursor|$line") {
                    [Microsoft.PowerShell.PSConsoleReadLine]::AcceptSuggestion($key, $arg)
                    $after = $null
                    [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState([ref]$after, [ref]$cursor)
                    $accepted = $after -cne $line
                }
                if ($accepted) {
                    $global:__hq_tab_cycle = $null
                } else {
                    [Microsoft.PowerShell.PSConsoleReadLine]::TabCompleteNext($key, $arg)
                    [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState([ref]$line, [ref]$cursor)
                    $global:__hq_tab_cycle = "$cursor|$line"
                }
            }
        }
        Set-PSReadLineKeyHandler @tabKey
    }
} catch {
    Write-Host "Tab으로 미리보기 받기를 켜지 못했습니다: $($_.Exception.Message)"
}
#endregion tab
Enter-PSSession -Session $global:__hq_session
