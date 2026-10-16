"""PowerShell 명령에 들어가는 값의 형식 검사.

host와 username은 접속 스크립트와 TrustedHosts 스크립트에 들어가므로, 따옴표·$·공백 같은
문자가 아예 들어오지 못하게 허용 문자만 받는다(값 자체는 Base64로도 감싸서 넘긴다).
"""

import ipaddress
import re

_HOSTNAME_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_DIGITS_AND_DOTS = re.compile(r"[0-9.]+")
_USER = re.compile(r"[A-Za-z0-9._-]{1,64}")
_PREFIX = re.compile(r"\.|[A-Za-z0-9._-]{1,64}")


class ValidationError(ValueError):
    """화면에 그대로 보여 줄 한국어 메시지를 담는다."""


def validate_host(value: str) -> str:
    host = value.strip()
    if not host:
        raise ValidationError("IP/호스트를 입력하세요.")
    if len(host) > 253:
        raise ValidationError("IP/호스트가 너무 깁니다(253자 이하).")
    if _DIGITS_AND_DOTS.fullmatch(host):
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            raise ValidationError(f"IPv4 주소 형식이 아닙니다: {host}") from None
        return host
    if not all(_HOSTNAME_LABEL.fullmatch(label) for label in host.split(".")):
        raise ValidationError(f"호스트 이름에는 영문, 숫자, 점(.), 하이픈(-)만 쓸 수 있습니다: {host}")
    return host


def validate_username(value: str) -> str:
    user = value.strip()
    if not user:
        raise ValidationError("ID를 입력하세요.")
    prefix, sep, name = user.rpartition("\\")
    if sep and not _PREFIX.fullmatch(prefix):
        raise ValidationError(f"ID 앞부분은 '.\\' 또는 '서버이름\\' 형식만 쓸 수 있습니다: {user}")
    if not _USER.fullmatch(name):
        raise ValidationError(f"ID에는 영문, 숫자, 점(.), 밑줄(_), 하이픈(-)만 쓸 수 있습니다(64자 이하): {user}")
    return user


def validate_ipv4(value: str) -> str:
    text = value.strip()
    try:
        return str(ipaddress.IPv4Address(text))
    except ValueError:
        raise ValidationError(f"IPv4 주소 형식이 아닙니다: {text}") from None
