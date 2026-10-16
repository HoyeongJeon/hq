from pathlib import Path

VENDOR = Path(__file__).resolve().parents[1] / "src" / "hq" / "static" / "vendor" / "xterm"


def test_xterm_files_are_bundled():
    for name in ("xterm.js", "xterm.css", "addon-fit.js", "LICENSE"):
        assert (VENDOR / name).stat().st_size > 0, name
    assert "Terminal" in (VENDOR / "xterm.js").read_text(encoding="utf-8")
    assert "FitAddon" in (VENDOR / "addon-fit.js").read_text(encoding="utf-8")
    assert "MIT" in (VENDOR / "LICENSE").read_text(encoding="utf-8")
