# hq: 이 PC의 WinRM TrustedHosts에 서버를 추가한다. 관리자 권한(UAC)으로 실행된다.
# 리스너를 만들지 않으므로(Enable-PSRemoting, winrm quickconfig를 쓰지 않음) 이 PC로 들어오는 포트는 열리지 않는다.
# WinRM 서비스는 TrustedHosts를 읽고 쓰려고 켤 뿐 시작 유형은 바꾸지 않는다(서비스가 꺼져 있어도 원격 접속은 된다, V4).
# 종료 코드: 0 성공, 2 서버 목록 읽기 실패, 3 WinRM 서비스 시작 실패, 4 TrustedHosts 읽기 실패,
# 5 TrustedHosts 쓰기 실패. 그 밖의 오류는 PowerShell이 1로 끝낸다.
$ErrorActionPreference = 'Stop'
try { $wanted = __HQ_HOSTS__ | ConvertFrom-Json } catch { exit 2 }
try {
    $service = Get-Service -Name WinRM
    if ($service.Status -ne 'Running') { Start-Service -Name WinRM }
} catch { exit 3 }
try { $current = (Get-Item -Path WSMan:\localhost\Client\TrustedHosts).Value } catch { exit 4 }
$have = @()
if ($current) { foreach ($item in $current.Split(',')) { $have += $item.Trim().ToLowerInvariant() } }
$add = @()
foreach ($h in $wanted) { if ($have -notcontains $h.ToLowerInvariant()) { $add += $h } }
if ($add.Count -gt 0) {
    try { Set-Item -Path WSMan:\localhost\Client\TrustedHosts -Value ($add -join ',') -Concatenate -Force } catch { exit 5 }
}
exit 0
