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
    } catch { }
    [Environment]::Exit($code)
}

# 세션 만들기 실패를 hq 종료 코드로 바꾼다.
# 10 로그인 거부, 11 응답 없음, 12 TrustedHosts 없음, 13 동시 세션 한도, 1 그 밖
function Get-HqExitCode {
    param($ErrorRecord)
    $id = "$($ErrorRecord.FullyQualifiedErrorId)"
    $message = "$($ErrorRecord.Exception.Message)"
    if ($id -like 'AccessDenied*') { return 10 }
    if ($id -like 'ServerNotTrusted*' -or $message -match 'TrustedHosts') { return 12 }
    if ($message -match 'concurrent shells|MaxShellsPerUser') { return 13 }
    if ($id -like 'CannotConnect*' -or $id -like 'WinRMOperationTimeout*' -or $id -like 'ComputerNotFound*') { return 11 }
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
Enter-PSSession -Session $global:__hq_session
