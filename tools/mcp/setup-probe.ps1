# HQ MCP 체크포인트 A(스펙 15장): 서버에 접속하지 않는 가짜 MCP 서버를 C:\dev\hq-mcp-probe에만 등록한다.
# 이 PC의 PowerShell에서 실행:
#   powershell -ExecutionPolicy Bypass -File <저장소>\tools\mcp\setup-probe.ps1 [-AllowBypass]
# -AllowBypass를 주면 권한 설정에서 disableBypassPermissionsMode와 disableAutoMode 두 줄을 뺀다(A-3).
# 가짜 서버는 이 저장소의 지금 파일로 돈다. 끝나면 remove-probe.ps1로 지운다.
param([switch]$AllowBypass)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$probeDir = 'C:\dev\hq-mcp-probe'
$outside = Join-Path $env:TEMP 'hq-probe-outside.txt'

$settingsDir = Join-Path $probeDir '.claude'
New-Item -ItemType Directory -Force -Path $settingsDir | Out-Null
$source = Join-Path $PSScriptRoot 'settings.local.json'
$settings = Join-Path $settingsDir 'settings.local.json'
if ($AllowBypass) {
    $all = @(Get-Content -LiteralPath $source -Encoding UTF8)
    $kept = @($all | Where-Object { $_ -notmatch '"disable(BypassPermissions|Auto)Mode"' })
    if ($kept.Count -ne $all.Count - 2) { throw "settings.local.json에서 뺄 두 줄을 찾지 못했습니다." }
    # Claude Code가 읽는 JSON이므로 BOM 없이 쓴다
    [IO.File]::WriteAllLines($settings, [string[]]$kept, (New-Object System.Text.UTF8Encoding $false))
} else {
    Copy-Item -LiteralPath $source -Destination $settings -Force
}

# A-4에서 작업 폴더 밖 파일로 읽게 한다
Set-Content -LiteralPath $outside -Value 'outside' -Encoding ASCII

Push-Location $probeDir
try {
    & claude mcp add --scope local --transport stdio hq -- uv run --project $repo python "$repo\tools\mcp\probe_server.py"
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($code -ne 0) {
    Write-Host "등록하지 못했습니다(claude 종료 코드 $code). 이미 등록되어 있으면 remove-probe.ps1을 먼저 실행하세요."
    exit 1
}

Write-Host ""
Write-Host "가짜 서버를 ${probeDir}에만 local 범위, 이름 hq로 등록했습니다."
if ($AllowBypass) {
    Write-Host "권한 설정에서 bypass와 auto 모드를 막는 두 줄을 뺐습니다(A-3)."
}
Write-Host "작업 폴더 밖 파일: $outside"
Write-Host ""
Write-Host "다음 할 일:"
Write-Host "  1. cd $probeDir"
if ($AllowBypass) {
    Write-Host "  2. claude --dangerously-skip-permissions"
} else {
    Write-Host "  2. claude"
}
Write-Host "  3. $repo\docs\mcp-acceptance.md의 체크포인트 A 항목을 차례로 확인합니다."
Write-Host "끝나면 Claude Code를 끝내고 이 PC의 PowerShell에서 다음을 실행합니다:"
Write-Host "  powershell -ExecutionPolicy Bypass -File $repo\tools\mcp\remove-probe.ps1"
