# HQ exe를 만든다: 빠른 테스트 → PyInstaller → dist\hq.exe
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
uv run pytest -m "not integration"
if ($LASTEXITCODE -ne 0) { throw '테스트가 실패해서 빌드하지 않았습니다.' }
uv run pyinstaller hq.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller가 실패했습니다.' }
Write-Host "만든 파일: $(Resolve-Path dist\hq.exe)"
