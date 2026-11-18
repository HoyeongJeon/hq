"""WSL의 Claude Code에 MCP를 등록하는 스크립트(tools/mcp/setup-wsl.sh)의 글자 검사. 스크립트를 실행하지 않는다."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "mcp" / "setup-wsl.sh"


def test_the_script_keeps_unix_line_ends_on_a_pc_that_converts_them():
    # core.autocrlf=true인 PC에서 CRLF로 풀리면 bash가 첫 줄부터 실패한다
    assert "tools/mcp/*.sh text eol=lf" in (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    data = SCRIPT.read_bytes()
    assert b"\r" not in data and data.startswith(b"#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in data.decode("utf-8")


def test_the_script_registers_only_in_its_own_folder_and_runs_the_windows_server():
    text = SCRIPT.read_text(encoding="utf-8")
    add = 'claude mcp add --scope local --transport stdio hq -- "$uv" run --project "$repo_win" python -m hq.mcp'
    assert add in text
    assert "--scope user" not in text and "--scope project" not in text
    assert "uv.exe" in text and "wslpath -w" in text
    # WSL이 아니면 아무것도 하지 않는다
    assert "WSL_DISTRO_NAME" in text and text.index("WSL_DISTRO_NAME") < text.index("claude mcp add")


def test_the_script_uses_the_permission_template_and_blocks_the_data_folder_by_its_wsl_path():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "tools/mcp/settings.local.json" in text and ".claude/settings.local.json" in text
    # 템플릿의 ~/AppData 규칙은 WSL에서 다른 폴더를 가리키므로 /mnt/c 경로로 한 번 더 막는다
    assert 'f"Read(/{data}/**)"' in text and 'f"Edit(/{data}/**)"' in text
    assert "LOCALAPPDATA" in text


def test_the_script_holds_no_real_address_and_no_user_folder():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "/mnt/c/Users/" not in text and "/home/" not in text
