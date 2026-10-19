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


def test_build_and_shortcut_scripts_exist():
    assert "pyinstaller hq.spec" in (ROOT / "scripts" / "build.ps1").read_text(encoding="utf-8-sig")
    shortcut = (ROOT / "scripts" / "make-shortcut.ps1").read_text(encoding="utf-8-sig")
    assert "09_앱_바로가기" in shortcut and "HQ.lnk" in shortcut
