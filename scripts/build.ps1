# HQ exe를 만든다: 빠른 테스트 → PyInstaller → dist\hq.exe
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

function Invoke-Native([scriptblock] $Command) {
    # 네이티브 명령(uv)의 성공 여부는 종료 코드($LASTEXITCODE)로만 판단한다. 이 스크립트의 출력을 돌려받으면(*>, 2>&1)
    # Windows PowerShell 5.1이 stderr 줄을 오류로 바꾸는데, 'Stop'이면 PyInstaller가 stderr에 쓰는 첫 INFO 줄에서
    # 멈춘다. 여기서 바꾼 값은 이 함수 안에서만 쓰인다
    $ErrorActionPreference = 'Continue'
    & $Command
}

Invoke-Native { uv run pytest -m "not integration" }
if ($LASTEXITCODE -ne 0) { throw '테스트가 실패해서 빌드하지 않았습니다.' }
Invoke-Native { uv run pyinstaller hq.spec --noconfirm --clean }
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller가 실패했습니다.' }
Write-Host "만든 파일: $(Resolve-Path dist\hq.exe)"
