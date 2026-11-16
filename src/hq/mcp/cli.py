"""MCP가 쓸 서버를 사람이 정하는 명령줄(list, on, off, never). 스펙 6장.

Claude가 아니라 사용자가 정한다. MCP 기능으로 내놓지 않는다. --all과 금지를 푸는 명령은 두지 않는다. 금지를 풀려면
mcp-access.json을 직접 고친다. host는 usable처럼 IP면 주소 값으로, 이름이면 대소문자 없이 비교한다.
네트워크를 쓰지 않는다(이름을 풀지 않는다). 이름 host와 금지 주소의 비교는 MCP가 실행 때 한다.
찍는 줄은 모두 shown을 거친다. 이름, 그룹, 사람이 친 인자에 든 제어 문자나 방향 제어 문자가 터미널 화면을 바꾸지 않는다.
"""

import contextlib
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import TextIO

from hq.inventory import UNGROUPED, Server
from hq.mcp.access import (
    Access,
    _forbidden_ip,
    _hidden_in,
    canonical_ip,
    hidden_name,
    load_access,
    load_servers,
    save_access,
    target_of,
    usable,
)

OK = 0
REFUSED = 2
ACCESS_FILE = "mcp-access.json"
INVENTORY_FILE = "inventory.json"
USAGE = """\
사용법:
  python -m hq.mcp list                 모든 서버와 상태(켜짐, 꺼짐, 금지, 쓸 수 없음)
  python -m hq.mcp on <host>...         켜기. host가 금지 IP인 서버는 거절한다
  python -m hq.mcp on --group <그룹>    그룹을 켜기. host가 금지 IP인 서버는 건너뛴다
  python -m hq.mcp off <host>...        끄기
  python -m hq.mcp never <IP>...        금지 목록에 넣고 끄기. 풀려면 mcp-access.json을 직접 고친다"""
UNCHANGED = "아무것도 바꾸지 않았습니다."
# access.usable이 쓰지 않는 이름(access.hidden_name: 별명이나 이름)
_HIDDEN_NAME = "이름에 제어 문자나 보이지 않는 글자, 글자 순서를 바꾸는 글자가 들어 있어"

Result = tuple[int, list[str]]  # (종료 코드, 찍을 줄. 줄마다 줄바꿈이 없다)


def main(argv: Sequence[str], *, data: Path, out: TextIO) -> int:
    """명령 하나를 실행하고 결과를 out에 찍는다. 0은 성공, 2는 거절. 파일은 data 폴더의 두 파일이다."""
    code, lines = _run(list(argv), data)
    for line in lines:
        print(shown(line), file=out)
    return code


def shown(text: str) -> str:
    """access._hidden_in이 보는 글자(제어, 서식, 줄 나눔, 사용자 정의, 정해지지 않은 글자, 보통이 아닌 빈칸, 채움
    글자)를 \\x1b, \\u202e, \\U000e0041처럼 적는다. 그대로 찍으면 ESC 시퀀스가 터미널 화면을 지우거나, 방향 제어
    문자가 글자 순서를 바꿔 보이게 한다."""
    return "".join(_escaped(char) if _hidden_in(char) else char for char in text)


def _escaped(char: str) -> str:
    code = ord(char)
    if code <= 0xFF:
        return f"\\x{code:02x}"
    if code <= 0xFFFF:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"


def _run(argv: list[str], data: Path) -> Result:
    command, args = (argv[0], argv[1:]) if argv else ("", [])
    if command == "list" and not args:
        return _list(data)
    if command == "on" and len(args) == 2 and args[0] == "--group":
        return _on_group(args[1], data)
    # host와 IP는 -로 시작하지 않으므로 --all 같은 것은 모두 사용법 오류다
    if command in ("on", "off", "never") and args and not any(arg.startswith("-") for arg in args):
        return {"on": _on, "off": _off, "never": _never}[command](args, data)
    return REFUSED, USAGE.splitlines()


def _list(data: Path) -> Result:
    servers, problem = load_servers(data / INVENTORY_FILE)
    if problem:
        return REFUSED, [problem]
    access = load_access(data / ACCESS_FILE)
    lines = [access.problem] if access.problem else []  # 깨진 설정은 모두 꺼짐으로 보인다
    on = {server.id for server in usable(servers, access)}
    for server in servers:
        why = ""
        if _forbidden_ip(server.host, access.never):
            state = "금지"
        elif hidden_name(server):
            state, why = "쓸 수 없음", f"  ({_HIDDEN_NAME} MCP에서 쓰지 않습니다. HQ에서 별명이나 이름을 고치세요.)"
        elif server.id in on:
            state = "켜짐"
        else:
            state = "꺼짐"
        lines.append(f"{state}  {target_of(server)}  {server.group or UNGROUPED}{why}")
    return OK, lines or ["등록된 서버가 없습니다."]


def _on(hosts: list[str], data: Path) -> Result:
    """하나라도 켤 수 없으면 아무것도 바꾸지 않는다."""
    access, servers, problem = _load(data)
    if problem:
        return REFUSED, [problem]
    by_key = {_key(server.host): server for server in servers}
    chosen: dict[str, Server] = {}
    refusals = []
    for host in hosts:
        server = by_key.get(_key(host))
        if server is None:
            refusals.append(f"서버 목록에 없습니다: {host}")
        elif reason := _why_not(server, access):
            refusals.append(f"{reason} 켜지 않습니다: {target_of(server)}")
        else:
            chosen[server.id] = server
    if refusals:
        return REFUSED, [*refusals, UNCHANGED]
    return OK, _enable(data, access, list(chosen.values()))


def _on_group(group: str, data: Path) -> Result:
    """그룹 이름은 HQ 화면과 같게 본다. 그룹이 빈 서버는 미분류다. 켤 수 없는 서버는 건너뛰고 알린다."""
    access, servers, problem = _load(data)
    if problem:
        return REFUSED, [problem]
    members = [server for server in servers if (server.group or UNGROUPED) == group.strip()]
    if not members:
        return REFUSED, [f"그룹에 서버가 없습니다: {group}"]
    lines, chosen = [], []
    for server in members:
        if reason := _why_not(server, access):
            lines.append(f"{reason} 건너뜁니다: {target_of(server)}")
        else:
            chosen.append(server)
    return OK, lines + _enable(data, access, chosen)


def _off(hosts: list[str], data: Path) -> Result:
    """enabled에서 host가 같은 항목을 뺀다. 서버 목록은 읽지 않으므로 목록에서 지운 서버의 항목도 뺄 수 있다."""
    access = load_access(data / ACCESS_FILE)
    if access.problem:
        return REFUSED, [access.problem]
    keys = {_key(host) for host in hosts}
    enabled = tuple(pair for pair in access.enabled if _key(pair[1]) not in keys)
    save_access(data / ACCESS_FILE, replace(access, enabled=enabled))
    was_on = {_key(host) for _, host in access.enabled}
    return OK, [f"껐습니다: {host}" if _key(host) in was_on else f"켜져 있지 않습니다: {host}" for host in hosts]


def _never(values: list[str], data: Path) -> Result:
    """금지 목록에 넣고 host가 그 주소인 항목을 enabled에서 뺀다. 하나라도 IP가 아니면 아무것도 바꾸지 않는다."""
    access = load_access(data / ACCESS_FILE)
    if access.problem:
        return REFUSED, [access.problem]
    ips, refusals = [], []
    for value in values:
        try:
            ips.append(canonical_ip(value))
        except ValueError:
            refusals.append(f"IP 주소가 아닙니다: {value}. 금지 목록에는 IP만 넣을 수 있습니다.")
    if refusals:
        return REFUSED, [*refusals, UNCHANGED]
    never = tuple(dict.fromkeys((*access.never, *ips)))
    enabled = tuple(pair for pair in access.enabled if not _forbidden_ip(pair[1], never))
    save_access(data / ACCESS_FILE, Access(enabled=enabled, never=never))
    lines = [f"금지 목록에 넣었습니다: {ip}" for ip in dict.fromkeys(ips)]
    return OK, lines + [f"껐습니다: {host}" for _, host in access.enabled if _forbidden_ip(host, never)]


def _load(data: Path) -> tuple[Access, list[Server], str | None]:
    """켜는 명령이 읽는 두 파일과 문제. 설정 파일이 깨졌으면 덮어쓰지 않도록 서버 목록보다 먼저 알린다."""
    access = load_access(data / ACCESS_FILE)
    if access.problem:
        return access, [], access.problem
    servers, problem = load_servers(data / INVENTORY_FILE)
    return access, servers, problem


def _enable(data: Path, access: Access, servers: list[Server]) -> list[str]:
    """servers를 지금 host로 켠다. 같은 서버의 예전 host 항목은 뺀다."""
    ids = {server.id for server in servers}
    kept = [pair for pair in access.enabled if pair[0] not in ids]
    enabled = (*kept, *((server.id, server.host.lower()) for server in servers))
    save_access(data / ACCESS_FILE, replace(access, enabled=enabled))
    return [f"켰습니다: {target_of(server)}" for server in servers]


def _why_not(server: Server, access: Access) -> str | None:
    """켤 수 없는 까닭. usable이 빼는 서버와 같다."""
    if _forbidden_ip(server.host, access.never):
        return "host가 금지 IP라"
    if hidden_name(server):
        return _HIDDEN_NAME
    return None


def _key(host: str) -> str:
    """host를 비교하는 글자. IP면 주소 값(canonical_ip), 이름이면 소문자."""
    with contextlib.suppress(ValueError):
        return canonical_ip(host)
    return host.strip().lower()
