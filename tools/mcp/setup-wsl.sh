#!/usr/bin/env bash
# WSL의 Claude Code에 HQ MCP 서버를 등록한다. 사용자가 WSL 터미널에서 직접 실행한다.
#
#   setup-wsl.sh                      전용 폴더(~/hq-ops)에 등록한다. 도구 셋 모두, Windows 쪽과 같은 권한 설정.
#   setup-wsl.sh --read-only <폴더>   다른 작업 폴더에 읽기 전용으로 등록한다. list_servers와 run_query만 되고
#                                     run_script는 그 폴더에서 막힌다. 그 폴더의 다른 설정과 모드는 그대로 둔다.
#
# MCP 서버는 Windows에서 돈다(저장된 비밀번호를 푸는 것과 PowerShell 원격 접속이 Windows 기능이다).
# WSL의 Claude Code가 Windows의 uv.exe를 자식으로 띄우고 표준 입출력으로 주고받는다.
# 등록은 언제나 한 폴더에만 local 범위로 한다.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"

mode="full"
target="$HOME/hq-ops"
if [ "$#" -eq 2 ] && [ "$1" = "--read-only" ]; then
    mode="read-only"
    target="$2"
elif [ "$#" -ne 0 ]; then
    echo "사용법: setup-wsl.sh  또는  setup-wsl.sh --read-only <폴더>. 아무것도 하지 않았습니다."
    exit 1
fi

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
if [ "$mode" = "read-only" ]; then
    if [ ! -d "$target" ]; then
        echo "폴더가 없습니다: $target. 아무것도 하지 않았습니다."
        exit 1
    fi
    target="$(cd "$target" && pwd -P)"
    case "$target/" in
        "$repo"/*)
            echo "HQ 저장소 안에는 등록하지 않습니다. 아무것도 하지 않았습니다."
            exit 1
            ;;
    esac
fi

repo_win="$(wslpath -w "$repo")"
# 서버 목록과 비밀번호 파일이 있는 Windows 폴더. 한글 사용자 이름이 깨지지 않게 UTF-8로 받는다
local_app="$(powershell.exe -NoProfile -Command '[Console]::OutputEncoding = [Text.Encoding]::UTF8; $env:LOCALAPPDATA' | tr -d '\r')"
if [ -z "$local_app" ]; then
    echo "Windows의 LOCALAPPDATA를 읽지 못했습니다. 아무것도 하지 않았습니다."
    exit 1
fi
data="$(wslpath -u "$local_app")/hq"

mkdir -p "$target/.claude"
settings="$target/.claude/settings.local.json"
# 설정을 쓰지 못하면 여기서 멈추고 등록하지 않는다
if [ "$mode" = "full" ]; then
    python3 "$repo/tools/mcp/wsl_settings.py" full "$settings" "$data" "$repo/tools/mcp/settings.local.json"
else
    python3 "$repo/tools/mcp/wsl_settings.py" read-only "$settings" "$data"
fi

cd "$target"
claude mcp remove --scope local hq >/dev/null 2>&1 || true
claude mcp add --scope local --transport stdio hq -- "$uv" run --project "$repo_win" python -m hq.mcp

echo
if [ "$mode" = "full" ]; then
    echo "HQ MCP 서버를 $target 에만 local 범위, 이름 hq로 등록했습니다."
else
    echo "HQ MCP 서버를 $target 에 읽기 전용으로 등록했습니다(local 범위, 이름 hq)."
    echo "run_script는 이 폴더에서 막혀 있습니다. 서버를 바꾸는 일은 ~/hq-ops 에서 하세요."
    echo "고치기 전 설정은 $settings.before-hq 에 있습니다(처음 고칠 때 있던 경우)."
fi
echo "다음 할 일:"
echo "  1. cd $target"
echo "  2. claude"
echo "  3. /mcp 로 hq가 연결되어 있는지 봅니다."
echo "지우려면 그 폴더에서: claude mcp remove --scope local hq"
