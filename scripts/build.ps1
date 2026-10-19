# HQ exe를 만든다: 빠른 테스트 → PyInstaller → dist\hq.exe
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

function Invoke-Native([scriptblock] $Command) {
    # 네이티브 명령(uv)의 성공 여부는 종료 코드($LASTEXITCODE)로만 판단한다. 이 스크립트의 출력을 돌려받으면(*>, 2>&1)
    # Windows PowerShell 5.1이 stderr 줄을 오류로 바꾸는데, 'Stop'이면 PyInstaller가 stderr에 쓰는 첫 INFO 줄에서
    # 멈춘다. 여기서 바꾼 값은 이 함수 안에서만 쓰인다.
    # uv를 띄우지 못하면(PATH에 없음, 실행이 막힘) 오류가 기록만 되고 넘어가며 종료 코드도 남지 않는다. 앞 명령이 남긴
    # 0으로 성공한 것처럼 보이지 않게 비워 두고, 그대로 비어 있으면 실패로 끝낸다. try는 부른 쪽의 try와 상관없이 늘
    # 여기서 이 오류를 받게 한다
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = $null
    $cause = ''
    try { & $Command } catch { $cause = " ($($_.Exception.Message))" }
    if ($null -eq $global:LASTEXITCODE) {
        throw "uv를 실행하지 못했습니다(설치되어 있지 않거나 PATH에 없거나 실행이 막혔습니다). 빌드하지 않았습니다.$cause"
    }
}

Invoke-Native { uv run pytest -m "not integration" }
if ($LASTEXITCODE -ne 0) { throw '테스트가 실패해서 빌드하지 않았습니다.' }
Invoke-Native { uv run pyinstaller hq.spec --noconfirm --clean }
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller가 실패했습니다.' }
Write-Host "만든 파일: $(Resolve-Path dist\hq.exe)"
