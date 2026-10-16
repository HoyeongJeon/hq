import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "src" / "hq" / "static" / "vendor" / "xterm"


def test_xterm_files_are_bundled():
    for name in ("xterm.js", "xterm.css", "addon-fit.js", "LICENSE"):
        assert (VENDOR / name).stat().st_size > 0, name
    assert "Terminal" in (VENDOR / "xterm.js").read_text(encoding="utf-8")
    assert "FitAddon" in (VENDOR / "addon-fit.js").read_text(encoding="utf-8")
    assert "MIT" in (VENDOR / "LICENSE").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(), reason="git 저장소가 아니다")
def test_git_keeps_the_vendored_bytes_as_they_are():
    # core.autocrlf=true인 PC에서도 받은 그대로의 바이트를 지키도록 줄 끝 변환을 끈다
    files = sorted(p.relative_to(ROOT).as_posix() for p in VENDOR.rglob("*") if p.is_file())
    out = subprocess.run(["git", "check-attr", "text", "--", *files], cwd=ROOT, capture_output=True, text=True,
                         check=True).stdout
    assert out.splitlines() == [f"{name}: text: unset" for name in files]
