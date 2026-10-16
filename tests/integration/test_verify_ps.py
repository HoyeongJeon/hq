from pathlib import Path

import pytest
from ps import parse_errors

from hq.verify import username_check_script

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def test_username_check_script_parses(tmp_path):
    assert parse_errors(username_check_script("10.0.1.21", ["Administrator"], r"C:\s.dpapi"), tmp_path) == []


def test_collect_errors_script_parses(tmp_path):
    text = (ROOT / "tools" / "verify" / "collect-errors.ps1").read_text(encoding="utf-8-sig")
    assert parse_errors(text, tmp_path) == []
