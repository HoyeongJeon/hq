import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_settings_file_is_the_json_in_the_spec():
    spec = (ROOT / "docs/specs/2026-11-04-hq-mcp-design.md").read_text(encoding="utf-8")
    block = re.search(r"settings\.local\.json`:\n\n```json\n(.*?)```", spec, re.S).group(1)
    assert json.loads((ROOT / "tools/mcp/settings.local.json").read_text(encoding="utf-8")) == json.loads(block)
