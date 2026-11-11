"""MCP가 쓸 수 있는 서버(mcp-access.json)와 Claude가 보는 대상 글자. 스펙 4.1장과 6장.

기본은 모든 서버 꺼짐이다. 설정 파일이 깨졌으면 모두 끈 것으로 보고 이유를 알리며, 깨진 파일은 덮어쓰지 않는다.
금지 주소(never)는 글자가 아니라 주소 값으로 비교한다. inventory.json은 읽기만 한다.
"""

import contextlib
import ipaddress
import json
import os
import re
import socket
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hq.fileops import read_text_with_retry, replace_with_retry
from hq.inventory import Inventory, InventoryError, Server

VERSION = 1
_KEYS = {"version", "enabled", "never"}
_ITEM_KEYS = {"id", "host"}
# C0, DEL, C1. 대상 글자는 확인 창과 결과에 그대로 찍히므로 화면을 지우는 ESC 시퀀스 같은 것이 든 별명은 쓰지 않는다
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
# HQ 창이 inventory.json을 바꿔치기하는 순간에 읽으면 WinError 5로 실패하고 곧 풀린다(스펙 3장). fileops와 같은 값
_READ_ATTEMPTS = 5
_READ_DELAY = 0.05


@dataclass(frozen=True)
class Access:
    enabled: tuple[tuple[str, str], ...] = ()  # (서버 id, 소문자 host)
    never: tuple[str, ...] = ()  # canonical_ip로 고친 글자
    problem: str | None = None  # 파일이 깨졌을 때의 이유. 이때 enabled와 never는 비어 있다


class AccessError(Exception):
    """사용 설정을 저장할 수 없을 때. 메시지는 화면에 그대로 보여 준다."""


def load_access(path: Path) -> Access:
    """사용 설정을 읽는다. 예외를 던지지 않는다. 파일이 없으면 모두 꺼짐, 깨졌으면 모두 꺼짐과 그 이유."""
    try:
        text = read_text_with_retry(path, encoding="utf-8-sig")  # 메모장이 붙인 BOM도 읽는다
    except FileNotFoundError:
        return Access()
    except (OSError, ValueError) as exc:  # 폴더이거나 계속 잠겨 있거나 UTF-8이 아니다
        return _broken(path, f"읽을 수 없습니다: {exc}")
    try:
        enabled, never = _parse(text)
    except ValueError as exc:
        return _broken(path, str(exc))
    return Access(enabled=enabled, never=never)


def _broken(path: Path, reason: str) -> Access:
    return Access(problem=f"사용 설정 파일에 문제가 있어 모든 서버를 끈 것으로 봅니다: {path} ({reason})")


def _parse(text: str) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    """스펙 6장의 모양인지 검사해 (enabled, never)를 만든다. 틀리면 무엇이 틀렸는지 담은 ValueError.

    Claude에게도 가는 글이므로 서버 id가 든 enabled 항목은 그대로 옮겨 적지 않는다.
    """
    try:
        data = json.loads(text, object_pairs_hook=_unique_keys)
    except (json.JSONDecodeError, RecursionError) as exc:  # 아주 깊게 겹친 괄호는 RecursionError
        raise ValueError(f"JSON 형식이 아닙니다: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError("맨 바깥이 {}로 감싼 JSON 객체가 아닙니다")
    version = data.get("version")
    if isinstance(version, bool) or version != VERSION:  # JSON true를 1로 보지 않는다
        raise ValueError(f"알 수 없는 version {version!r}")
    if data.keys() != _KEYS:
        # never가 빠지거나 이름을 잘못 적은 파일을 빈 목록으로 읽으면 금지 주소가 조용히 풀린다
        raise ValueError(f"항목은 version, enabled, never 세 개여야 합니다. 지금 항목: {sorted(data)}")
    if not isinstance(data["enabled"], list) or not isinstance(data["never"], list):
        raise ValueError("enabled와 never는 [ ]로 감싼 목록이어야 합니다")
    enabled = []
    for number, item in enumerate(data["enabled"], 1):
        if (
            not isinstance(item, dict)
            or item.keys() != _ITEM_KEYS
            or not all(isinstance(v, str) for v in item.values())
        ):
            raise ValueError(f"enabled의 {number}번째 항목은 id와 host 글자만 가져야 합니다")
        enabled.append((item["id"], item["host"].lower()))
    return tuple(enabled), tuple(_never_ip(value) for value in data["never"])


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """같은 항목이 두 번 있으면 ValueError. json은 뒤의 값만 남기므로 손으로 넣은 never 줄이 앞의 금지를 지운다."""
    keys = [key for key, _ in pairs]
    for key in keys:
        if keys.count(key) > 1:
            raise ValueError(f"같은 항목 {key!r}이(가) 두 번 있습니다")
    return dict(pairs)


def _never_ip(value: object) -> str:
    """never의 한 값. 이름을 받으면 DNS 결과가 바뀔 때 금지 서버가 새어 나가므로 IP만 받는다(스펙 6장)."""
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            return canonical_ip(value)
    raise ValueError(f"never에는 IP 주소만 넣을 수 있습니다: {value!r}")


def save_access(path: Path, access: Access) -> None:
    """사용 설정을 tmp에 쓰고 바꿔치기한다. 깨진 파일에서 읽은 설정이면 덮어쓰지 않고 AccessError."""
    if access.problem is not None:
        raise AccessError(f"사용 설정 파일을 덮어쓰지 않았습니다. {access.problem}")
    data = {
        "version": VERSION,
        "enabled": [{"id": server_id, "host": host} for server_id, host in access.enabled],
        "never": list(access.never),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    # 정전이 나도 본 파일이 옛 내용이나 새 내용 중 하나로 온전히 남도록, 바꿔치기 전에 내용을 디스크에 내린다
    with open(temp, "w", encoding="utf-8") as file:
        file.write(json.dumps(data, indent=2) + "\n")
        file.flush()
        os.fsync(file.fileno())
    replace_with_retry(temp, path)


def canonical_ip(text: str) -> str:
    """주소 값을 한 가지 글자로 고친다. IP가 아니면 ValueError. ::ffff:10.0.1.21처럼 IPv4를 감싼 IPv6는 IPv4로."""
    address = ipaddress.ip_address(text.strip())
    if isinstance(address, ipaddress.IPv6Address) and (mapped := address.ipv4_mapped) is not None:
        return str(mapped)
    return str(address)


def target_of(server: Server) -> str:
    """Claude가 대상을 지정하는 글자. 확인 창에 32자리 id 대신 보인다(스펙 4.1)."""
    return f"{server.display_name} ({server.host})"


def load_servers(inventory_path: Path) -> tuple[list[Server], str | None]:
    """서버 목록을 새로 읽는다(저장하지 않는다). (목록, 문제). 문제가 있으면 목록은 비어 있다."""
    if not inventory_path.exists():
        return [], f"서버 목록 파일이 없습니다: {inventory_path}. HQ에서 서버를 먼저 등록하세요."
    inventory = Inventory(inventory_path)
    attempts = _READ_ATTEMPTS
    while True:
        try:
            inventory.load()
            return inventory.servers, None
        except InventoryError as exc:
            attempts -= 1
            if attempts == 0 or not isinstance(exc.__cause__, PermissionError):
                return [], str(exc)
        time.sleep(_READ_DELAY)


def usable(servers: Sequence[Server], access: Access) -> list[Server]:
    """MCP가 쓸 수 있는 서버. host가 이름이면 never와의 비교는 실행 전에 resolve_host로 따로 한다."""
    enabled = set(access.enabled)
    return [
        server
        for server in servers
        if (server.id, server.host.lower()) in enabled
        and not _CONTROL.search(server.display_name)
        and not _forbidden_ip(server.host, access.never)
    ]


def _forbidden_ip(host: str, never: Sequence[str]) -> bool:
    try:
        return canonical_ip(host) in never
    except ValueError:  # 이름 host
        return False


def resolve_host(host: str, *, getaddrinfo=socket.getaddrinfo, timeout: float = 2.0) -> list[str] | None:
    """host의 모든 주소(canonical_ip, 중복 없이). host가 IP면 풀지 않는다. 풀지 못했거나 timeout초를 넘기면 None.

    getaddrinfo는 시간 한도를 받지 않으므로 데몬 스레드에서 부르고 timeout까지만 기다린다. 늦은 스레드는 혼자 끝난다.
    """
    with contextlib.suppress(ValueError):
        return [canonical_ip(host)]
    found = []

    def lookup() -> None:
        with contextlib.suppress(OSError, UnicodeError):  # 풀 수 없는 이름, idna로 바꿀 수 없는 이름
            found.append(getaddrinfo(host, None))

    thread = threading.Thread(target=lookup, name="hq-mcp-resolve", daemon=True)
    thread.start()
    thread.join(timeout)
    if not found:
        return None
    try:
        addresses = [canonical_ip(info[4][0]) for info in found[0]]
    except ValueError:  # 비교하지 못한 주소가 금지 주소일 수 있으므로 하나라도 그러면 풀지 못한 것으로 본다
        return None
    return list(dict.fromkeys(addresses)) or None
