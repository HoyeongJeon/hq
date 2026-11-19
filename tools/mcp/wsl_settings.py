"""setup-wsl.sh가 부른다. WSL의 한 폴더에 넣을 Claude Code 권한 설정(.claude/settings.local.json)을 만든다.

WSL의 python3로 돌기 때문에 표준 라이브러리만 쓴다.

  wsl_settings.py full <대상 파일> <자료 폴더> <템플릿>   전용 폴더용. 템플릿 그대로에 자료 폴더 차단을 더한다
  wsl_settings.py read-only <대상 파일> <자료 폴더>       다른 작업 폴더용. 있던 설정에 읽기 전용 규칙만 더한다
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys

READ_TOOLS = ["mcp__hq__list_servers", "mcp__hq__run_query"]
# deny는 allow와 ask보다 먼저 적용되므로 그 폴더에 넓은 허용 규칙이 있어도 run_script는 막힌다
READ_ONLY_DENY = ["mcp__hq__run_script", "Bash(*hq.mcp*)", "Bash(*mcp-access*)"]


def _data_rules(data: str) -> list[str]:
    # 절대 경로 규칙은 //로 시작한다. data가 /mnt/...이므로 앞에 /를 하나 더 붙인다
    return [f"Read(/{data}/**)", f"Edit(/{data}/**)"]


def _add(rules: list, more: list[str]) -> list:
    return rules + [rule for rule in more if rule not in rules]


def full(template: dict, data: str) -> dict:
    settings = copy.deepcopy(template)
    settings["permissions"]["deny"] = _add(settings["permissions"]["deny"], _data_rules(data))
    return settings


def read_only(existing: dict, data: str) -> dict:
    """있던 설정은 그대로 두고 규칙만 더한다. 모드(bypass, auto)는 건드리지 않는다."""
    settings = copy.deepcopy(existing)
    permissions = settings.setdefault("permissions", {})
    permissions["allow"] = _add(permissions.get("allow", []), READ_TOOLS)
    permissions["deny"] = _add(permissions.get("deny", []), READ_ONLY_DENY + _data_rules(data))
    return settings


def _well_formed(settings: object) -> bool:
    if not isinstance(settings, dict):
        return False
    permissions = settings.get("permissions", {})
    if not isinstance(permissions, dict):
        return False
    return all(isinstance(permissions.get(key, []), list) for key in ("allow", "deny"))


def _write(target: str, settings: dict) -> None:
    with open(target, "w", encoding="utf-8") as file:
        json.dump(settings, file, ensure_ascii=False, indent=2)
        file.write("\n")


def main(argv: list[str]) -> int:
    mode, target, data = argv[:3]
    if mode == "full":
        with open(argv[3], encoding="utf-8") as file:
            _write(target, full(json.load(file), data))
        return 0
    existing: object = {}
    if os.path.exists(target):
        try:
            with open(target, encoding="utf-8") as file:
                existing = json.load(file)
        except (OSError, ValueError):
            existing = None
        if not _well_formed(existing):
            print(f"{target} 을 읽을 수 없거나 모양이 예상과 다릅니다. 고치지 않았습니다.")
            return 1
        backup = target + ".before-hq"
        if not os.path.exists(backup):
            shutil.copyfile(target, backup)
    _write(target, read_only(existing, data))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
