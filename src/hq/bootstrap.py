"""탭에서 실행할 PowerShell 명령을 만들고, 그 프로세스의 종료 코드를 끝난 이유로 바꾼다."""

import base64
import json
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from hq.paths import resource
from hq.validation import validate_host, validate_username

MAIN_MARKER = "#region main"
_PLACEHOLDER = "__HQ_PARAMS__"

# 준비 신호(spec 7.3). 부트스트랩이 안전장치를 건 직후 콘솔 제목을 이 글자로 바꾸면 ConPTY가 화면에 보이지 않는
# 제목 시퀀스로 내보낸다. ':'를 넣어 host, ID, Windows 경로가 될 수 없게 했다(이것들은 화면에 찍힐 수 있다).
READY_MARKER = "hq:safety-ready"
# 준비 신호가 오지 않아 hq가 프로세스를 끝냈을 때 hq가 정하는 종료 코드. 스크립트는 이 코드로 끝나지 않는다.
NOT_READY_CODE = 30


class ExitReason(StrEnum):
    USER_EXIT = "USER_EXIT"
    AUTH_FAILED = "AUTH_FAILED"
    UNREACHABLE = "UNREACHABLE"
    NOT_TRUSTED = "NOT_TRUSTED"
    QUOTA = "QUOTA"
    SECRET_UNREADABLE = "SECRET_UNREADABLE"
    DISCONNECTED = "DISCONNECTED"
    NOT_READY = "NOT_READY"
    UNKNOWN = "UNKNOWN"


_REASONS = {
    0: ExitReason.USER_EXIT,
    10: ExitReason.AUTH_FAILED,
    11: ExitReason.UNREACHABLE,
    12: ExitReason.NOT_TRUSTED,
    13: ExitReason.QUOTA,
    14: ExitReason.SECRET_UNREADABLE,
    20: ExitReason.DISCONNECTED,
    NOT_READY_CODE: ExitReason.NOT_READY,
}


def reason_for_exit(code: int | None) -> ExitReason:
    if code is None:
        return ExitReason.UNKNOWN
    return _REASONS.get(code, ExitReason.UNKNOWN)


@dataclass(frozen=True)
class BootstrapParams:
    host: str
    username: str
    nickname: str
    secret_path: str


def ps_json_literal(value: object) -> str:
    """value를 JSON으로 바꿔 PowerShell 작은따옴표 문자열로 감싼다. 스크립트는 `<문자열> | ConvertFrom-Json`으로 읽는다.

    ensure_ascii가 ASCII 밖의 글자(한글, PowerShell이 작은따옴표로 치는 U+2018~U+201B 포함)를 모두 \\uXXXX로 바꾸고,
    `'`와 DEL도 \\u0027, \\u007f로 바꾼다. 따옴표 안에는 출력 가능한 ASCII만 남아 스크립트 문법과 섞일 수 없다.
    Base64를 쓰지 않는 이유: 백신(AhnLab V3)이 FromBase64String과 Invoke-Command가 함께 든 명령의 실행을 막는다.
    """
    text = json.dumps(value, ensure_ascii=True).replace("'", "\\u0027").replace("\x7f", "\\u007f")
    return f"'{text}'"


def template_text() -> str:
    return resource("ps/bootstrap.ps1").read_text(encoding="utf-8-sig")


def prelude() -> str:
    """안전장치(prompt)와 오류 분류 함수만. 테스트가 접속 없이 이 부분을 돌린다."""
    text = template_text()
    return text[: text.index(MAIN_MARKER)]


def build_script(params: BootstrapParams) -> str:
    payload = {
        "host": validate_host(params.host),
        "username": validate_username(params.username),
        "nickname": params.nickname,
        "secret_path": params.secret_path,
    }
    return template_text().replace(_PLACEHOLDER, ps_json_literal(payload))


def powershell_exe() -> str:
    root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    return str(Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def encoded_argv(script: str, *, no_exit: bool = True) -> list[str]:
    argv = [powershell_exe(), "-NoLogo", "-NoProfile"]
    if no_exit:
        argv.append("-NoExit")
    argv += ["-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode("ascii")]
    return argv


def build_command(params: BootstrapParams) -> list[str]:
    return encoded_argv(build_script(params))
