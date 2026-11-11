# HQ MCP 체크포인트 A를 끝낸다: setup-probe.ps1이 한 hq 등록, C:\dev\hq-mcp-probe 폴더, 임시 파일을 지운다.
# Claude Code를 끝낸 뒤 이 PC의 PowerShell에서 실행:
#   powershell -ExecutionPolicy Bypass -File <저장소>\tools\mcp\remove-probe.ps1
# probe-log.txt도 함께 지워지므로 protocolVersion은 먼저 적어 둔다.

$ErrorActionPreference = 'Stop'
$probeDir = 'C:\dev\hq-mcp-probe'
$outside = Join-Path $env:TEMP 'hq-probe-outside.txt'

# local 범위 등록은 그 폴더에서만 지울 수 있다. 폴더를 먼저 지웠어도 등록을 지울 수 있게 다시 만든다
New-Item -ItemType Directory -Force -Path $probeDir | Out-Null
Push-Location $probeDir
try {
    & claude mcp remove hq --scope local
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
Remove-Item -LiteralPath $probeDir -Recurse -Force
if (Test-Path -LiteralPath $outside) {
    Remove-Item -LiteralPath $outside -Force
}

Write-Host ""
if ($code -ne 0) {
    Write-Host "폴더와 임시 파일은 지웠지만 등록은 지우지 못했습니다(claude 종료 코드 $code). 이미 지워졌을 수 있습니다."
    exit 1
}
Write-Host "가짜 서버 등록과 $probeDir, $outside 를 지웠습니다."
