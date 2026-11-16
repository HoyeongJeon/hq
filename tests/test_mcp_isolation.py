"""MCP 서버와 창이 서로의 코드를 가져오지 않고, MCP가 의존성을 늘리지 않았다(스펙 3장, 14장).

모듈마다 새 파이썬 프로세스에서 가져와 본다. 이 테스트 프로세스는 이미 여러 모듈을 가져왔으므로 여기서 보면 틀린다.
"""

import json
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def imported_by(module):
    code = f"import {module}, sys, json; print(json.dumps(sorted(m for m in sys.modules if m.startswith('hq.'))))"
    return json.loads(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout)


def test_the_mcp_server_never_loads_the_window_code():
    assert not {"hq.bridge", "hq.app", "hq.sessions"} & set(imported_by("hq.mcp.server"))


def test_the_mcp_entry_point_never_loads_the_window_code():
    assert not {"hq.bridge", "hq.app", "hq.sessions"} & set(imported_by("hq.mcp.__main__"))


def test_the_window_never_loads_the_mcp_server():
    assert [m for m in imported_by("hq.app") if m.startswith("hq.mcp")] == []


def test_no_new_dependency_was_added():
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    assert sorted(d.split(">=")[0] for d in deps) == ["openpyxl", "pywebview", "pywinpty"]
