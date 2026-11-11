# MCP 실행(스펙 8.2, 8.3). 스크립트 하나를 서버 하나에서 실행한다. 세션을 열고, 스크립트를 글자로 서버에 보내
# 서버에서 스크립트 블록으로 만들어 작업으로 돌린다. 0.2초마다 작업의 기록을 옮기고, 시간 한도와 취소 파일을 지킨다.
#
# 입력(표준 입력, ASCII JSON): {"host", "username", "secret_path", "script", "timeout_seconds", "cancel_path"}
# 출력: 스크립트의 출력, 정보(Write-Host), 경고(앞에 'WARNING: ')는 표준 출력으로, 오류는 표준 오류로 쓴다. 진행,
# 자세한 정보, 디버그 기록은 버린다. 로더가 PowerShell 스트림을 모두 버리므로 [Console]::Out과 [Console]::Error로만
# 쓴다. 외부 프로그램과 $Host.UI는 쓰지 않는다. 비밀번호를 읽지 못했을 때는 경로와 예외 글자를 찍지 않는다.
#
# 스크립트는 PowerShell에 붙여 넣은 것과 같은 뜻으로 돈다: 문장 하나가 실패하면 그 오류를 남기고 다음 문장으로 간다
# (상태 OK). 스크립트 전체가 오류로 끝나야(throw, $ErrorActionPreference = 'Stop') 2다.
#
# 종료 코드: 0 끝남, 1 세션이 열려 있지 않음(스크립트를 보내지 않음), 2 스크립트 오류, 10~13 세션 만들기 실패
# (부트스트랩의 Get-HqExitCode, 그 밖은 1), 14 비밀번호를 읽지 못함, 20 실행 중 세션이 끊김, 21 시간 한도, 22 취소.
# 잡지 않은 오류는 로더가 70으로 끝낸다. [Environment]::Exit는 finally를 건너뛰므로 세션을 연 뒤에는 Exit-HqRun으로만
# 끝낸다(세션을 지우고 쓰기를 비운 뒤 끝낸다).
#
# 시간 한도는 템플릿이 시작할 때부터 잰다(세션을 여는 시간도 든다). 그래야 Python이 프로세스에 더 준 10초를
# 작업 멈추기와 세션 지우기에 온전히 쓴다.

$hqClock = [Diagnostics.Stopwatch]::StartNew()
$ErrorActionPreference = 'Stop'
$p = [Console]::In.ReadToEnd() | ConvertFrom-Json
$session = $null

# 1. 세션 만들기 실패를 종료 코드로 바꾸는 Get-HqExitCode. 옆의 bootstrap.ps1에서 그 구역을 읽어 정의한다
# (분류 규칙을 두 벌로 만들지 않는다)
$hqBootstrap = [IO.File]::ReadAllText(
    [IO.Path]::Combine([IO.Path]::GetDirectoryName($hqTemplate), 'bootstrap.ps1'), [Text.Encoding]::UTF8)
$hqStart = $hqBootstrap.IndexOf('#region exitcode', [StringComparison]::Ordinal)
$hqEnd = $hqBootstrap.IndexOf('#endregion exitcode', $hqStart, [StringComparison]::Ordinal)
. ([scriptblock]::Create($hqBootstrap.Substring($hqStart, $hqEnd - $hqStart)))

function Write-HqStreams {
    # 작업에 쌓인 기록을 비우며(ReadAll) 옮긴다. 출력은 서버에서 Out-String이 만든 줄이다
    foreach ($item in $hqChild.Output.ReadAll()) { [Console]::Out.WriteLine([string]$item) }
    foreach ($item in $hqChild.Information.ReadAll()) { [Console]::Out.WriteLine([string]$item.MessageData) }
    foreach ($item in $hqChild.Warning.ReadAll()) { [Console]::Out.WriteLine('WARNING: ' + $item.Message) }
    foreach ($item in $hqChild.Error.ReadAll()) { [Console]::Error.WriteLine([string]$item) }
    # 진행, 자세한 정보, 디버그 기록은 옮기지 않는다. 비우기만 해서 실행 내내 쌓이지 않게 한다
    [void]$hqChild.Progress.ReadAll()
    [void]$hqChild.Verbose.ReadAll()
    [void]$hqChild.Debug.ReadAll()
}

function Exit-HqRun([int]$code) {
    if ($null -ne $session) {
        try { Remove-PSSession -Session $session } catch { }
    }
    [Console]::Out.Flush()
    [Console]::Error.Flush()
    [Environment]::Exit($code)
}

function Stop-HqRun([int]$code) {
    # 서버의 파이프라인을 멈추고, 멈추기 전에 온 기록까지 옮긴 뒤 세션을 지우고 끝낸다
    try { Stop-Job -Job $hqJob } catch { }
    Write-HqStreams
    Exit-HqRun $code
}

# 2. 비밀번호 파일로 자격 증명을 만든다. 실패하면 경로와 예외 글자 없이 한 줄만 찍고, 세션 구역에 닿기 전에 끝낸다
try {
    $hqSecret = (Get-Content -LiteralPath $p.secret_path -Raw).Trim()
    $cred = New-Object System.Management.Automation.PSCredential($p.username, (ConvertTo-SecureString -String $hqSecret))
} catch {
    [Console]::Error.WriteLine('저장된 비밀번호를 읽을 수 없습니다.')
    [Environment]::Exit(14)
}

# 3. 세션. 시험은 이 구역만 이 PC 안의 세션으로 바꾼다. 실패 메시지는 그대로 찍는다. 비밀번호 파일 경로는 이 명령에
# 가지 않고, 사용자 이름도 들지 않는다: 이 PC의 WinRM 메시지(WsmRes.dll)는 값을 끼워 넣지 않고, 실서버 표본
# (tests/integration/test_bootstrap_ps.py의 CASES)에는 서버 주소만 있다(2026-11-11 확인)
#region session
try {
    $session = New-PSSession -ComputerName $p.host -Credential $cred -Authentication Negotiate
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    [Environment]::Exit((Get-HqExitCode $_))
}
#endregion session

# 4. 세션이 열려 있지 않으면 스크립트에 닿지 않고 끝낸다
if ($null -eq $session -or "$($session.State)" -ne 'Opened') {
    [Console]::Error.WriteLine('세션이 열려 있지 않아 스크립트를 보내지 않았습니다.')
    Exit-HqRun 1
}

try {
    # 5. 스크립트는 글자로 보내 서버에서 스크립트 블록으로 만든다. 이 PC에서 만들면 스크립트의 using 변수가 이
    # 템플릿의 변수($cred 등)를 읽어 서버로 가져간다(스펙 8.2). -Session이 빠지면 이 PC에서 실행된다
    $hqJob = Invoke-Command -Session $session -AsJob -ArgumentList $p.script -ScriptBlock { param($t) & ([scriptblock]::Create($t)) | Out-String -Stream -Width 200 }
    $hqChild = $hqJob.ChildJobs[0]

    # 6. 끝날 때까지 0.2초마다 기록을 옮긴다. 취소 파일이 생겼거나 시간 한도를 넘으면 작업을 멈춘다
    $hqFinished = @('Completed', 'Failed', 'Stopped')
    while ($hqFinished -notcontains "$($hqChild.JobStateInfo.State)") {
        if ([IO.File]::Exists($p.cancel_path)) { Stop-HqRun 22 }
        if ($hqClock.Elapsed.TotalSeconds -ge $p.timeout_seconds) { Stop-HqRun 21 }
        Start-Sleep -Milliseconds 200
        Write-HqStreams
    }
    Write-HqStreams

    # 7. 끝난 모양. 실패했으면 그 이유를 찍고, 세션이 열려 있지 않으면 실행 중에 끊긴 것이다
    if ("$($hqChild.JobStateInfo.State)" -eq 'Completed') { Exit-HqRun 0 }
    $hqReason = $hqChild.JobStateInfo.Reason
    if ($null -ne $hqReason) { [Console]::Error.WriteLine($hqReason.Message) }
    if ("$($session.State)" -ne 'Opened') { Exit-HqRun 20 }
    Exit-HqRun 2
} catch {
    # 예상하지 못한 오류에도 세션을 남기지 않는다. 다시 던져 로더가 종류와 줄 번호만 찍고 70으로 끝내게 한다
    try { Remove-PSSession -Session $session } catch { }
    throw
}
