"""조회 검사 시험의 표. 공격 표는 스펙 파일에서 읽어 표가 두 벌이 되지 않게 한다.

여기의 스크립트는 검사에만 넘긴다. 어떤 시험에서도 실행하지 않는다.
"""

from pathlib import Path

SPEC = Path(__file__).resolve().parents[2] / "docs" / "specs" / "2026-11-04-hq-mcp-design.md"

# 스펙 15장의 "파서를 부르지 않고 거절해야 하는 표". 둘째 줄이 두 줄에 걸쳐 있어 스펙에서 줄 단위로 읽지 않는다
NO_PARSE = [
    "using module \\\\z\\share\\m",
    "using `\nmodule \\\\z\\share\\m",
    "using <# c #> module \\\\z\\share\\m",
    "configuration C { Import-DscResource -ModuleName \\\\z\\share\\m }",
]

# 통과해야 하는 표(스펙 15장). Claude가 흔히 쓰는 모양으로 열 개 조회를 모두 덮는다. 스크립트는 Claude가 쓰는 모양
# 그대로 두므로 긴 줄이 있는 문자열은 끝에서 E501만 끈다(여러 줄 문자열의 noqa는 닫는 따옴표 뒤에 둔다)
PASS_SCRIPTS = {
    "memory": """$ErrorActionPreference = 'Stop'
$os = Get-CimInstance Win32_OperatingSystem
[pscustomobject]@{
    TotalGB = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
    FreeGB  = [math]::Round($os.FreePhysicalMemory / 1MB, 1)
    UsedPct = [math]::Round(100 * ($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / $os.TotalVisibleMemorySize, 1)
} | ConvertTo-Json""",
    "disk": """Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" |
    Select-Object DeviceID, @{n='SizeGB';e={[math]::Round($_.Size/1GB,1)}}, @{n='FreeGB';e={[math]::Round($_.FreeSpace/1GB,1)}} |
    Format-Table -AutoSize""",  # noqa: E501
    "process": "Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 10 Name, Id, @{n='MB';e={[int]($_.WorkingSet64/1MB)}}",  # noqa: E501
    "service": """Get-CimInstance Win32_Service | ? { $_.State -eq 'Running' -and $_.PathName -notlike 'C:\\Windows\\*' } |
    select Name, StartName, PathName | fl""",  # noqa: E501
    "eventlog": """$since = (Get-Date).AddDays(-7)
Get-WinEvent -FilterHashtable @{ LogName = 'Security'; Id = 4625; StartTime = $since } -MaxEvents 200 -ErrorAction SilentlyContinue |
    ForEach-Object {
        $data = ([xml]$_.ToXml()).Event.EventData.Data
        [pscustomobject]@{
            Time = $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss')
            User = ($data | Where-Object { $_.Name -eq 'TargetUserName' }).'#text'
            Ip   = ($data | Where-Object { $_.Name -eq 'IpAddress' }).'#text'
        }
    } | Group-Object Ip | Sort-Object Count -Descending | Select-Object -First 20 Count, Name""",  # noqa: E501
    "network": """Get-NetTCPConnection -State Listen |
    Select-Object LocalAddress, LocalPort, @{n='Process';e={(Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).Name}} |
    Sort-Object LocalPort
netstat -ano | Select-String 'LISTENING'""",  # noqa: E501
    "tasks": """Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\\Microsoft\\*' } | ForEach-Object {
    $info = $_ | Get-ScheduledTaskInfo
    [pscustomobject]@{ Name = $_.TaskName; State = "$($_.State)"; LastRun = $info.LastRunTime
        Run = ($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join '; ' }
}""",
    "autoruns": """$keys = 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run', 'Registry::HKEY_USERS\\*\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run'
foreach ($k in $keys) { Get-ItemProperty -Path $k -ErrorAction SilentlyContinue | Select-Object * -ExcludeProperty PS* | Format-List }""",  # noqa: E501
    "recent_files": """Get-ChildItem C:\\inetpub -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -gt (Get-Date).AddDays(-3) } | Sort-Object LastWriteTime -Descending | Select-Object -First 50 |
    ForEach-Object { [pscustomobject]@{ Path = $_.FullName; Size = $_.Length; SHA256 = (Get-FileHash $_.FullName -Algorithm SHA256).Hash } }""",  # noqa: E501
    "accounts": """Get-LocalUser | Select-Object Name, Enabled, LastLogon, PasswordLastSet
try { Get-LocalGroupMember -Group 'Administrators' -ErrorAction Stop | Select-Object Name, ObjectClass }
catch [System.Exception] { Write-Host "실패: $($_.Exception.Message)" }""",
    "idioms": """$ProgressPreference = 'SilentlyContinue'
$null = Get-Date
$rows = foreach ($n in 'a', 'b') { if (Test-Path "C:\\$n") { $n.ToUpper() } else { '{0:N1}' -f 1.26 } }
$i = 0; $i++; $i += 2
[int]$count = @($rows).Count
$text = ('a,b' -split ',') -join ';' -replace 'a', 'c'
[datetime]::Now.AddHours(-1) | Out-Null
[string]::IsNullOrEmpty($text); [regex]::Matches('a1b2', '\\d').Count; '5' -as [int]
switch ($count) { 0 { 'none' } default { 'some' } }
Get-ChildItem C:\\nope 2>$null""",
    "crlf_hangul": "Write-Host '메모리 조회'\r\nGet-Date -Format 'yyyy-MM-dd'\r\n",
}


def spec_attacks() -> list[str]:
    """스펙 15장 "조회 검사 공격 표" 바로 아래 powershell 코드 블록의 줄들. 한 줄이 스크립트 하나다."""
    text = SPEC.read_text(encoding="utf-8")
    after = text.split("조회 검사 공격 표", 1)[1]
    before, rest = after.split("```powershell\n", 1)
    assert "```" not in before, "공격 표 바로 아래의 코드 블록이 powershell 블록이 아니다"
    return [line for line in rest.split("```", 1)[0].splitlines() if line.strip()]
