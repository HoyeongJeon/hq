# HQ 검증 V3·V4: 오류 표본과 이 PC의 WinRM 상태를 모은다.
# 일반 PowerShell(관리자 아님)에서 실행:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\verify\collect-errors.ps1 -ComputerName 10.0.1.21
# 결과는 현재 폴더의 hq-verify-report.txt 에 저장된다. 비밀번호는 저장하지 않는다.
param([Parameter(Mandatory = $true)][string]$ComputerName)

$report = New-Object System.Collections.Generic.List[string]
function Add-Line([string]$text) { $report.Add($text); Write-Host $text }
function Format-Error($record) {
    "FQID={0} | ErrorCode={1} | HResult={2} | 메시지={3}" -f $record.FullyQualifiedErrorId, $record.Exception.ErrorCode, $record.Exception.HResult, ($record.Exception.Message -replace '\s+', ' ')
}
function Test-Open([string]$label, [scriptblock]$open) {
    try {
        $session = & $open
        Add-Line "[$label] 성공"
        Remove-PSSession $session
    } catch {
        Add-Line "[$label] 실패: $(Format-Error $_)"
    }
}

Add-Line "== V4: 이 PC의 WinRM =="
$service = Get-Service -Name WinRM
Add-Line ("WinRM 서비스: {0} / 시작 유형: {1}" -f $service.Status, $service.StartType)
try {
    $value = (Get-Item -Path WSMan:\localhost\Client\TrustedHosts -ErrorAction Stop).Value
    Add-Line "WSMan 드라이브로 TrustedHosts 읽기: 성공 '$value'"
} catch {
    Add-Line "WSMan 드라이브로 TrustedHosts 읽기: 실패 $(Format-Error $_)"
}
foreach ($keyPath in @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WSMAN\Client', 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WinRM\Client')) {
    try {
        $key = Get-Item -Path $keyPath -ErrorAction Stop
        foreach ($name in $key.GetValueNames()) { Add-Line ("레지스트리 {0} : {1} = '{2}'" -f $keyPath, $name, $key.GetValue($name)) }
        if ($key.GetValueNames().Count -eq 0) { Add-Line "레지스트리 $keyPath : 값 없음" }
    } catch {
        Add-Line "레지스트리 $keyPath : 키 없음"
    }
}

$cred = Get-Credential -Message "$ComputerName 접속 계정(비밀번호는 저장하지 않습니다)"

Add-Line "== V4: 서비스가 '$($service.Status)' 상태에서 정상 접속 =="
Test-Open '정상 접속' { New-PSSession -ComputerName $ComputerName -Credential $cred -ErrorAction Stop }

Add-Line "== V3: 오류 표본 =="
$wrong = ConvertTo-SecureString ('wrong-' + [guid]::NewGuid().ToString()) -AsPlainText -Force
$badCred = New-Object System.Management.Automation.PSCredential($cred.UserName, $wrong)
Test-Open '틀린 비밀번호' { New-PSSession -ComputerName $ComputerName -Credential $badCred -ErrorAction Stop }
Test-Open 'TrustedHosts 없음' { New-PSSession -ComputerName 192.0.2.10 -Credential $cred -ErrorAction Stop }
Test-Open '포트 닫힘' { New-PSSession -ComputerName $ComputerName -Port 5999 -Credential $cred -ErrorAction Stop }

$answer = Read-Host "동시 세션 한도 시험을 할까요? 세션을 최대 35개 열었다가 모두 닫습니다(2008 R2 서버 권장) [y/N]"
if ($answer -eq 'y') {
    $opened = @()
    try {
        for ($i = 1; $i -le 35; $i++) { $opened += New-PSSession -ComputerName $ComputerName -Credential $cred -ErrorAction Stop }
        Add-Line "[동시 세션 한도] 35개까지 열림(한도 오류 없음)"
    } catch {
        Add-Line "[동시 세션 한도] $($opened.Count + 1)번째에서 실패: $(Format-Error $_)"
    } finally {
        if ($opened.Count -gt 0) { $opened | Remove-PSSession }
    }
}

$out = Join-Path (Get-Location) 'hq-verify-report.txt'
$report | Set-Content -LiteralPath $out -Encoding UTF8
Write-Host ""
Write-Host "보고서: $out"
Write-Host "이 파일 내용을 Claude에게 붙여 주세요. 비밀번호는 들어 있지 않습니다."
