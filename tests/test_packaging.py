import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# pywinpty의 네이티브 파일. _winpty.pyd가 conpty.dll·winpty.dll을 읽고, 그 둘이 자기 옆의
# OpenConsole.exe·winpty-agent.exe를 띄운다
WINPTY_NATIVE = ("OpenConsole.exe", "conpty.dll", "winpty-agent.exe", "winpty.dll")


def run_spec() -> dict:
    """hq.spec을 PyInstaller 대신 가짜 Analysis·PYZ·EXE로 돌려 넘긴 값을 받는다."""
    seen = {}

    class Analysis:
        def __init__(self, scripts, **options):
            seen["scripts"] = scripts
            seen["analysis"] = options
            self.pure = self.scripts = self.binaries = self.datas = []

    namespace = {
        "Analysis": Analysis,
        "PYZ": lambda *tocs, **options: None,
        "EXE": lambda *parts, **options: seen.setdefault("exe", options),
        "SPECPATH": str(ROOT),
    }
    spec = ROOT / "hq.spec"
    exec(compile(spec.read_text(encoding="utf-8"), str(spec), "exec"), namespace)
    return seen


def test_spec_bundles_the_screen_scripts_and_icon_without_a_console():
    spec = (ROOT / "hq.spec").read_text(encoding="utf-8")
    for source, target in (("src/hq/static", "hq/static"), ("src/hq/ps", "hq/ps"), ("src/hq/assets", "hq/assets")):
        assert f'("{source}", "{target}")' in spec
    assert "console=False" in spec
    assert 'name="hq"' in spec


def test_spec_puts_the_pywinpty_native_files_next_to_its_module():
    # pywinpty에는 PyInstaller hook이 없다. winpty/에 이 파일들이 없으면 exe에서 탭(ConPTY)을 열지 못한다
    binaries = run_spec()["analysis"]["binaries"]
    bundled = {Path(source).name: (Path(source), target) for source, target in binaries}
    winpty_dir = Path(importlib.util.find_spec("winpty").origin).parent
    for name in WINPTY_NATIVE:
        source, target = bundled[name]
        assert source == winpty_dir / name and source.is_file(), source
        assert target == "winpty", (name, target)


def test_spec_makes_one_windowed_exe_named_hq_with_the_icon():
    exe = run_spec()["exe"]
    assert exe["console"] is False
    assert exe["name"] == "hq"
    assert (ROOT / exe["icon"]).is_file()


def test_build_and_shortcut_scripts_exist():
    assert "pyinstaller hq.spec" in (ROOT / "scripts" / "build.ps1").read_text(encoding="utf-8-sig")
    shortcut = (ROOT / "scripts" / "make-shortcut.ps1").read_text(encoding="utf-8-sig")
    assert "09_앱_바로가기" in shortcut and "HQ.lnk" in shortcut


def test_the_shortcut_runs_a_copy_that_only_this_account_can_change():
    # dist\hq.exe는 C:\에서 Authenticated Users:(M)을 물려받는다. 다른 계정이 바꿔 넣은 exe를 바로가기로 실행하면
    # 그 코드가 내 권한으로 돌아 DPAPI 비밀번호를 모두 풀 수 있다(spec 8.1). 그래서 내 계정만 쓸 수 있는 폴더에
    # 복사해 거기를 가리킨다
    text = (ROOT / "scripts" / "make-shortcut.ps1").read_text(encoding="utf-8-sig")
    assert "$folder = Join-Path $env:LOCALAPPDATA 'Programs\\hq'" in text
    assert "$installed = Join-Path $folder 'hq.exe'" in text
    assert "$link.TargetPath = $installed" in text
    assert "$link.WorkingDirectory = $folder" in text
    assert '$link.IconLocation = "$installed,0"' in text
    # 쓰기 권한 검사: 상속된 항목까지 SID로 본다. 허용은 내 계정, SYSTEM, Administrators뿐이다. 권한은 바꾸지 않는다
    assert "GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])" in text
    assert "[Security.Principal.WindowsIdentity]::GetCurrent().User.Value" in text
    assert "'S-1-5-18'" in text and "'S-1-5-32-544'" in text
    assert "Set-Acl" not in text and "icacls" not in text
    # 복사 전에: 설치 폴더부터 사용자 프로필 폴더까지 모든 폴더의 권한, 이미 설치된 exe의 권한(덮어써도 그 파일의
    # 권한이 남는다), 실행 중인지. 복사한 뒤 바로가기를 만들기 전에: 복사한 exe의 권한
    assert "$profileRoot = [Environment]::GetFolderPath('UserProfile')" in text
    copy = text.index("Copy-Item")
    assert text.index("Assert-OnlyMineCanWriteUpTo $folder $profileRoot") < copy
    assert text.index("if (Test-Path -LiteralPath $installed) { Assert-OnlyMineCanWrite $installed }") < copy
    assert text.index("Get-Process -Name hq") < copy
    assert copy < text.rindex("Assert-OnlyMineCanWrite $installed") < text.index("CreateShortcut")
    assert "HQ를 먼저 닫" in text
