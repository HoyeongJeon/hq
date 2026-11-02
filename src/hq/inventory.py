"""서버 목록(inventory.json). 비밀번호는 여기에 두지 않는다(secret_store 참고)."""

import json
import os
import re
import shutil
import threading
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from hq.validation import ValidationError, is_hex_id, validate_host, validate_ipv4, validate_username

VERSION = 1
UNGROUPED = "미분류"
_LIMITS = {"name": ("이름", 100), "nickname": ("별명", 50), "group": ("그룹", 50), "memo": ("메모", 4000)}
_LABELS = {"id": "서버 id", "host": "IP/호스트", "username": "ID", **{k: label for k, (label, _) in _LIMITS.items()}}
# C0(U+0000~U+001F), DEL, C1(U+0080~U+009F). 이 값들은 화면과 원격 프롬프트(별명)에 그대로 찍히므로, ESC로 시작하는
# 시퀀스가 화면을 지우거나 터미널이 응답을 입력으로 보내게 할 수 있다. 메모만 줄바꿈과 탭을 둔다
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_MEMO_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# 짝 없는 UTF-16 반쪽. 화면의 JS 문자열에는 올 수 있는데, UTF-8로 저장할 수 없어 저장이 모두 막힌다
_SURROGATE = re.compile("[\ud800-\udfff]")


@dataclass
class Server:
    id: str
    name: str
    host: str
    username: str
    nickname: str = ""
    group: str = ""
    memo: str = ""

    @property
    def display_name(self) -> str:
        return self.nickname or self.name


class InventoryError(Exception):
    """서버 목록 파일을 읽거나 쓸 수 없을 때. 메시지는 화면에 그대로 보여 준다."""


def _text(raw: Mapping[str, Any], key: str) -> str:
    """raw[key]의 글자. 없거나 None이면 "". 글자가 아니거나 짝 없는 서로게이트가 들어 있으면 ValidationError."""
    value = raw.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError(f"{_LABELS[key]}에 글자가 아닌 값이 들어 있습니다.")
    if _SURROGATE.search(value):
        raise ValidationError(f"{_LABELS[key]}에 쓸 수 없는 깨진 글자가 들어 있습니다. 다시 입력하세요.")
    return value


def clean_fields(raw: Mapping[str, Any], *, allow_controls: bool = False) -> dict[str, str]:
    """화면·엑셀에서 받은 값을 검사해 저장할 모양으로 만든다. 문제가 있으면 ValidationError.

    allow_controls: 제어 문자를 막지 않는다. 저장된 목록을 읽을 때만 쓴다. 예전 hq는 이름의 줄바꿈(엑셀의 Alt+Enter)이나
    탭을 받아 저장했으므로, 그 파일을 깨진 파일로 막지 않고 읽는다. 고쳐 저장하는 화면에서는 막는다.
    """
    if not isinstance(raw, Mapping):
        raise ValidationError("서버 정보의 형식이 잘못되었습니다.")
    fields = {
        "name": _text(raw, "name").strip(),
        "nickname": _text(raw, "nickname").strip(),
        "group": _text(raw, "group").strip(),
        "memo": _text(raw, "memo").replace("\r\n", "\n").replace("\r", "\n").strip(),
    }
    if not fields["name"]:
        raise ValidationError("이름을 입력하세요.")
    for key, (label, limit) in _LIMITS.items():
        if len(fields[key]) > limit:
            raise ValidationError(f"{label}은(는) {limit}자 이하로 입력하세요.")
        if allow_controls:
            continue
        if key == "memo" and _MEMO_CONTROL.search(fields[key]):
            raise ValidationError(f"{label}에는 줄바꿈과 탭 말고는 제어 문자(ESC 등)를 쓸 수 없습니다.")
        if key != "memo" and _CONTROL.search(fields[key]):
            raise ValidationError(f"{label}에는 줄바꿈, 탭, ESC 같은 제어 문자를 쓸 수 없습니다.")
    fields["host"] = validate_host(_text(raw, "host"))
    fields["username"] = validate_username(_text(raw, "username"))
    return fields


class Inventory:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._servers: list[Server] = []
        self._admin_ips: list[str] = []
        self._broken = False
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def _bak(self) -> Path:
        return self._path.with_name(self._path.name + ".bak")

    @property
    def servers(self) -> list[Server]:
        with self._lock:
            return [replace(s) for s in self._servers]

    @property
    def admin_ips(self) -> list[str]:
        with self._lock:
            return list(self._admin_ips)

    def load(self) -> None:
        with self._lock:
            self._servers, self._admin_ips, self._broken = [], [], False
            if not self._path.exists():
                return
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if data.get("version") != VERSION:
                    raise ValueError(f"알 수 없는 버전 {data.get('version')!r}")
                servers = [self._server_from(item) for item in data.get("servers", [])]
                ips = [validate_ipv4(ip) for ip in data.get("settings", {}).get("admin_ips", [])]
                hosts = [s.host.lower() for s in servers]
                if len(hosts) != len(set(hosts)):
                    raise ValueError("같은 IP/호스트가 두 번 있습니다")
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                self._broken = True
                raise InventoryError(
                    f"서버 목록 파일을 읽을 수 없어 저장을 막았습니다: {self._path} ({exc}). "
                    f"직전 버전은 {self._bak} 에 있습니다."
                ) from exc
            self._servers, self._admin_ips = servers, ips

    @staticmethod
    def _server_from(item: Mapping[str, Any]) -> Server:
        server_id = _text(item, "id")
        if not is_hex_id(server_id):
            raise ValueError(f"잘못된 서버 id {server_id!r}")
        return Server(id=server_id, **clean_fields(item, allow_controls=True))

    def _find(self, server_id: str) -> Server:
        for server in self._servers:
            if server.id == server_id:
                return server
        raise KeyError(server_id)

    def get(self, server_id: str) -> Server:
        with self._lock:
            return replace(self._find(server_id))

    def find_by_host(self, host: str) -> Server | None:
        key = host.strip().lower()
        with self._lock:
            for server in self._servers:
                if server.host.lower() == key:
                    return replace(server)
        return None

    def _check_host_free(self, host: str, except_id: str | None) -> None:
        key = host.lower()
        for server in self._servers:
            if server.id != except_id and server.host.lower() == key:
                raise ValidationError(f"이미 등록된 IP/호스트입니다: {host} ({server.display_name})")

    def add(self, raw: Mapping[str, Any]) -> Server:
        fields = clean_fields(raw)
        with self._lock:
            self._check_host_free(fields["host"], None)
            server = Server(id=uuid.uuid4().hex, **fields)
            self._servers.append(server)
            return replace(server)

    def update(self, server_id: str, raw: Mapping[str, Any]) -> Server:
        fields = clean_fields(raw)
        with self._lock:
            server = self._find(server_id)
            self._check_host_free(fields["host"], server_id)
            for key, value in fields.items():
                setattr(server, key, value)
            return replace(server)

    def remove(self, server_id: str) -> Server:
        with self._lock:
            server = self._find(server_id)
            self._servers.remove(server)
            return server

    def set_admin_ips(self, ips: Sequence[str]) -> None:
        cleaned = [validate_ipv4(ip) for ip in ips if ip.strip()]
        with self._lock:
            self._admin_ips = list(dict.fromkeys(cleaned))

    def save(self) -> None:
        with self._lock:
            if self._broken:
                raise InventoryError(f"서버 목록 파일이 깨져 있어 저장하지 않았습니다: {self._path}")
            data = {
                "version": VERSION,
                "settings": {"admin_ips": self._admin_ips},
                "servers": [asdict(s) for s in self._servers],
            }
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temp = self._path.with_name(self._path.name + ".tmp")
            # 정전이 나도 본 파일이 옛 내용이나 새 내용 중 하나로 온전히 남도록, 바꿔치기 전에 내용을 디스크에 내린다.
            with open(temp, "w", encoding="utf-8") as file:
                file.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
                file.flush()
                os.fsync(file.fileno())
            if self._path.exists():
                shutil.copy2(self._path, self._bak)
            os.replace(temp, self._path)
