# 바탕화면 09_앱_바로가기 폴더에 HQ 바로가기를 만든다.
$ErrorActionPreference = 'Stop'
$exe = (Resolve-Path (Join-Path (Split-Path $PSScriptRoot -Parent) 'dist\hq.exe')).Path
$folder = Join-Path ([Environment]::GetFolderPath('Desktop')) '09_앱_바로가기'
New-Item -ItemType Directory -Force -Path $folder | Out-Null
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut((Join-Path $folder 'HQ.lnk'))
$link.TargetPath = $exe
$link.WorkingDirectory = Split-Path $exe
$link.IconLocation = "$exe,0"
$link.Description = 'HQ: 서버 PowerShell 콘솔'
$link.Save()
Write-Host "바로가기: $(Join-Path $folder 'HQ.lnk')"
