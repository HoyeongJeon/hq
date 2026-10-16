from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOM = b"\xef\xbb\xbf"


def test_user_run_ps1_files_are_utf8_with_bom():
    files = sorted([*ROOT.glob("tools/**/*.ps1"), *ROOT.glob("scripts/*.ps1")])
    assert files, "사용자가 직접 실행하는 .ps1이 하나도 없다"
    for path in files:
        assert path.read_bytes().startswith(BOM), f"{path}: Windows PowerShell 5.1이 한글을 읽으려면 BOM이 필요하다"


def test_templates_read_by_python_have_no_bom():
    for path in sorted((ROOT / "src" / "hq" / "ps").glob("*.ps1")):
        assert not path.read_bytes().startswith(BOM), path
