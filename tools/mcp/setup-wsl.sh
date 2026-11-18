#!/usr/bin/env bash
# WSL의 Claude Code에 HQ MCP 서버를 등록한다. 사용자가 WSL 터미널에서 직접 실행한다.
#
# MCP 서버는 Windows에서 돈다(저장된 비밀번호를 푸는 것과 PowerShell 원격 접속이 Windows 기능이다).
# WSL의 Claude Code가 Windows의 uv.exe를 자식으로 띄우고 표준 입출력으로 주고받는다.
# 등록은 작업 폴더(~/hq-ops)에만 local 범위로 하고, Windows 쪽과 같은 권한 설정을 그 폴더에 넣는다.
set -euo pipefail

ops="$HOME/hq-ops"
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [ -z "${WSL_DISTRO_NAME:-}" ]; then
    echo "WSL 안에서 실행하세요. 아무것도 하지 않았습니다."
    exit 1
fi
uv="$(command -v uv.exe || true)"
if [ -z "$uv" ]; then
    echo "uv.exe를 찾지 못했습니다(WSL의 PATH에 Windows 경로가 있어야 합니다). 아무것도 하지 않았습니다."
    exit 1
fi
if ! command -v claude >/dev/null; then
    echo "claude 명령을 찾지 못했습니다. claude가 실행되는 터미널에서 다시 실행하세요. 아무것도 하지 않았습니다."
    exit 1
fi

repo_win="$(wslpath -w "$repo")"
# 서버 목록과 비밀번호 파일이 있는 Windows 폴더. 한글 사용자 이름이 깨지지 않게 UTF-8로 받는다
local_app="$(powershell.exe -NoProfile -Command '[Console]::OutputEncoding = [Text.Encoding]::UTF8; $env:LOCALAPPDATA' | tr -d '\r')"
if [ -z "$local_app" ]; then
    echo "Windows의 LOCALAPPDATA를 읽지 못했습니다. 아무것도 하지 않았습니다."
    exit 1
fi
data="$(wslpath -u "$local_app")/hq"

mkdir -p "$ops/.claude"
# 템플릿의 ~/AppData 규칙은 WSL에서 다른 폴더를 가리킨다. 실제 폴더를 /mnt 경로로 한 번 더 막는다
python3 - "$repo/tools/mcp/settings.local.json" "$ops/.claude/settings.local.json" "$data" <<'PY'
import json
import sys

source, target, data = sys.argv[1:4]
with open(source, encoding="utf-8") as file:
    settings = json.load(file)
settings["permissions"]["deny"] += [f"Read(/{data}/**)", f"Edit(/{data}/**)"]
with open(target, "w", encoding="utf-8") as file:
    json.dump(settings, file, ensure_ascii=False, indent=2)
PY

cd "$ops"
claude mcp remove --scope local hq >/dev/null 2>&1 || true
claude mcp add --scope local --transport stdio hq -- "$uv" run --project "$repo_win" python -m hq.mcp

echo
echo "HQ MCP 서버를 $ops 에만 local 범위, 이름 hq로 등록했습니다."
echo "다음 할 일:"
echo "  1. cd $ops"
echo "  2. claude"
echo "  3. /mcp 로 hq가 연결되어 있는지 봅니다."
echo "지우려면 그 폴더에서: claude mcp remove --scope local hq"
