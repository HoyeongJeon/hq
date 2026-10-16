from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOM = b"\xef\xbb\xbf"


def test_user_run_ps1_files_are_utf8_with_bom():
    files = sorted([*ROOT.glob("tools/**/*.ps1"), *ROOT.glob("scripts/*.ps1")])
    assert files, "사용자가 직접 실행하는 .ps1이 하나도 없다"
    for path in files:
        assert path.read_bytes().startswith(BOM), f"{path}: Windows PowerShell 5.1이 한글을 읽으려면 BOM이 필요하다"


def test_collect_errors_opens_every_session_like_the_bootstrap():
    # src/hq/ps/bootstrap.ps1과 같은 접속 설정이어야 V3 오류 표본이 HQ 탭에서 볼 오류와 같다.
    text = (ROOT / "tools" / "verify" / "collect-errors.ps1").read_text(encoding="utf-8-sig")
    calls = [line for line in text.splitlines() if "New-PSSession" in line and not line.lstrip().startswith("#")]
    assert len(calls) == 5, calls  # 정상 접속, 틀린 비밀번호, TrustedHosts 없음, 포트 닫힘, 동시 세션 한도
    for line in calls:
        assert "-Authentication Negotiate" in line, line


def test_templates_read_by_python_have_no_bom():
    for path in sorted((ROOT / "src" / "hq" / "ps").glob("*.ps1")):
        assert not path.read_bytes().startswith(BOM), path
