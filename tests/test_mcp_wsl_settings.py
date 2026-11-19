"""WSL 등록 스크립트가 쓰는 권한 설정 만들기(tools/mcp/wsl_settings.py). 파일만 다루고 아무것도 실행하지 않는다."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = "/mnt/c/Users/someone/AppData/Local/hq"


def _load():
    spec = importlib.util.spec_from_file_location("wsl_settings", ROOT / "tools" / "mcp" / "wsl_settings.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wsl_settings = _load()


def _template() -> dict:
    return json.loads((ROOT / "tools" / "mcp" / "settings.local.json").read_text(encoding="utf-8"))


def test_full_keeps_the_template_and_blocks_the_data_folder_by_its_wsl_path():
    settings = wsl_settings.full(_template(), DATA)
    permissions = settings["permissions"]
    assert permissions["ask"] == ["mcp__hq__run_script"]
    assert permissions["disableBypassPermissionsMode"] == "disable" and permissions["disableAutoMode"] == "disable"
    assert permissions["deny"][: len(_template()["permissions"]["deny"])] == _template()["permissions"]["deny"]
    assert f"Read(/{DATA}/**)" in permissions["deny"] and f"Edit(/{DATA}/**)" in permissions["deny"]


def test_read_only_denies_run_script_and_allows_only_the_two_read_tools():
    permissions = wsl_settings.read_only({}, DATA)["permissions"]
    assert permissions["allow"] == ["mcp__hq__list_servers", "mcp__hq__run_query"]
    assert "mcp__hq__run_script" in permissions["deny"]
    assert "ask" not in permissions
    assert f"Read(/{DATA}/**)" in permissions["deny"] and f"Edit(/{DATA}/**)" in permissions["deny"]
    # 그 폴더의 Claude가 셸로 서버를 켜거나 금지를 고치지 못하게 한다
    assert "Bash(*hq.mcp*)" in permissions["deny"] and "Bash(*mcp-access*)" in permissions["deny"]


def test_read_only_does_not_change_the_modes_or_other_settings_of_the_folder():
    existing = {
        "agent": "architect",
        "permissions": {"allow": ["Bash(chmod:*)"], "deny": ["Read(./secret/**)"], "defaultMode": "acceptEdits"},
    }
    settings = wsl_settings.read_only(existing, DATA)
    assert settings["agent"] == "architect"
    permissions = settings["permissions"]
    assert permissions["defaultMode"] == "acceptEdits"
    assert "disableBypassPermissionsMode" not in permissions and "disableAutoMode" not in permissions
    assert permissions["allow"] == ["Bash(chmod:*)", "mcp__hq__list_servers", "mcp__hq__run_query"]
    assert permissions["deny"][0] == "Read(./secret/**)"
    assert existing["permissions"]["allow"] == ["Bash(chmod:*)"]  # 받은 것을 고치지 않는다


def test_read_only_twice_adds_nothing_the_second_time():
    once = wsl_settings.read_only({}, DATA)
    assert wsl_settings.read_only(once, DATA) == once


@pytest.mark.parametrize("existing", [[], {"permissions": []}, {"permissions": {"allow": "Bash"}}])
def test_main_refuses_settings_of_an_unexpected_shape(tmp_path, existing):
    target = tmp_path / "settings.local.json"
    target.write_text(json.dumps(existing), encoding="utf-8")
    assert wsl_settings.main(["read-only", str(target), DATA]) == 1
    assert json.loads(target.read_text(encoding="utf-8")) == existing


def test_main_keeps_a_copy_of_the_file_it_changes(tmp_path):
    target = tmp_path / "settings.local.json"
    target.write_text('{"permissions": {"allow": ["Bash(chmod:*)"]}}', encoding="utf-8")
    assert wsl_settings.main(["read-only", str(target), DATA]) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["permissions"]["allow"][0] == "Bash(chmod:*)"
    backup = tmp_path / "settings.local.json.before-hq"
    assert json.loads(backup.read_text(encoding="utf-8")) == {"permissions": {"allow": ["Bash(chmod:*)"]}}
    # 다시 돌려도 처음 것을 덮어쓰지 않는다
    assert wsl_settings.main(["read-only", str(target), DATA]) == 0
    assert json.loads(backup.read_text(encoding="utf-8")) == {"permissions": {"allow": ["Bash(chmod:*)"]}}


def test_main_refuses_a_file_it_cannot_read_and_leaves_it_alone(tmp_path, capsys):
    target = tmp_path / "settings.local.json"
    target.write_text("{not json", encoding="utf-8")
    assert wsl_settings.main(["read-only", str(target), DATA]) == 1
    assert target.read_text(encoding="utf-8") == "{not json"
    assert "고치지 않았습니다" in capsys.readouterr().out


def test_main_writes_the_full_settings_from_the_template(tmp_path):
    target = tmp_path / "settings.local.json"
    template = ROOT / "tools" / "mcp" / "settings.local.json"
    assert wsl_settings.main(["full", str(target), DATA, str(template)]) == 0
    assert json.loads(target.read_text(encoding="utf-8")) == wsl_settings.full(_template(), DATA)
