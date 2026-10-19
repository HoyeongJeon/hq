# HQ를 내 계정만 쓸 수 있는 폴더(%LOCALAPPDATA%\Programs\hq)에 복사하고, 바탕화면 09_앱_바로가기 폴더에 그 exe의
# 바로가기를 만든다. 다시 빌드한 뒤에는 이 스크립트를 다시 실행해야 새 exe가 복사된다.
$ErrorActionPreference = 'Stop'
$built = Join-Path (Split-Path $PSScriptRoot -Parent) 'dist\hq.exe'
if (-not (Test-Path -LiteralPath $built -PathType Leaf)) {
    throw "빌드한 exe가 없습니다: $built. 먼저 scripts\build.ps1을 실행하세요."
}
$folder = Join-Path $env:LOCALAPPDATA 'Programs\hq'
$installed = Join-Path $folder 'hq.exe'

# 다른 계정이 바꿔 넣을 수 있는 exe를 바로가기로 실행하면 그 코드가 내 권한으로 돌아 DPAPI 비밀번호를 모두 풀 수 있다.
# 그래서 내 계정, SYSTEM, Administrators 말고는 누구도 쓸 수 없어야 한다. 권한은 이 스크립트가 바꾸지 않는다.
$allowed = @(
    [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    'S-1-5-18'      # NT AUTHORITY\SYSTEM
    'S-1-5-32-544'  # BUILTIN\Administrators
)
# 쓰기, 덧붙이기, 확장 속성 쓰기, 하위 항목 삭제, 속성 쓰기, 삭제, 권한 바꾸기, 소유권 가져오기, GENERIC_ALL, GENERIC_WRITE
$writeRights = 0x2 -bor 0x4 -bor 0x10 -bor 0x40 -bor 0x100 -bor 0x10000 -bor 0x40000 -bor 0x80000 -bor 0x10000000 -bor 0x40000000

function Get-AccountName([Security.Principal.SecurityIdentifier] $Sid) {
    try { $Sid.Translate([Security.Principal.NTAccount]).Value } catch { $Sid.Value }
}

function Assert-OnlyMineCanWrite([string] $Path) {
    # 상속된 항목까지 모두 본다. 소유자는 언제든 권한을 바꿀 수 있으므로 소유자도 본다
    $acl = Get-Acl -LiteralPath $Path
    $others = @(foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -ne 'Allow') { continue }
        if (([int64] $rule.FileSystemRights -band $writeRights) -eq 0) { continue }
        if ($allowed -contains $rule.IdentityReference.Value) { continue }
        Get-AccountName $rule.IdentityReference
    })
    $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier])
    if ($allowed -notcontains $owner.Value) { $others += "$(Get-AccountName $owner)(소유자)" }
    if ($others) {
        $names = ($others | Select-Object -Unique) -join ', '
        throw ("{0}에 내 계정, SYSTEM, Administrators 말고도 쓸 수 있는 계정이 있습니다: {1}. " +
            "다른 계정이 HQ를 바꿔 넣을 수 있으므로 여기서 멈춥니다(바로가기는 만들거나 고치지 않았습니다). " +
            "이 폴더의 권한을 먼저 확인하세요.") -f $Path, $names
    }
}

New-Item -ItemType Directory -Force -Path $folder | Out-Null
Assert-OnlyMineCanWrite $folder
$target = [IO.Path]::GetFullPath($installed)
$running = @(Get-Process -Name hq -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and [string]::Equals([IO.Path]::GetFullPath($_.Path), $target, [StringComparison]::OrdinalIgnoreCase)
})
if ($running) {
    throw "HQ가 $installed 로 실행 중이라 파일을 바꿀 수 없습니다. HQ를 먼저 닫고 다시 실행하세요."
}
Copy-Item -LiteralPath $built -Destination $installed -Force
Assert-OnlyMineCanWrite $installed

$shortcuts = Join-Path ([Environment]::GetFolderPath('Desktop')) '09_앱_바로가기'
New-Item -ItemType Directory -Force -Path $shortcuts | Out-Null
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut((Join-Path $shortcuts 'HQ.lnk'))
$link.TargetPath = $installed
$link.WorkingDirectory = $folder
$link.IconLocation = "$installed,0"
$link.Description = 'HQ: 서버 PowerShell 콘솔'
$link.Save()
Write-Host "설치한 파일: $installed"
Write-Host "바로가기: $(Join-Path $shortcuts 'HQ.lnk')"
