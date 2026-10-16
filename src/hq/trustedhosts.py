"""이 PC의 WinRM TrustedHosts: 읽기, 포함 여부 판단, 관리자 권한(UAC)으로 추가."""

import ctypes
import fnmatch
import logging
import subprocess
import winreg
from collections.abc import Callable, Iterable
from ctypes import wintypes
from enum import StrEnum

from hq.bootstrap import encoded_argv, ps_json_literal
from hq.paths import resource
from hq.validation import validate_host

_log = logging.getLogger(__name__)

CLIENT_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\WSMAN\Client"
CLIENT_VALUE = "trusted_hosts"
POLICY_KEY = r"SOFTWARE\Policies\Microsoft\Windows\WinRM\Client"
POLICY_VALUE = "TrustedHostsList"
_MISSING = object()
_PLACEHOLDER = "__HQ_HOSTS__"
# add-trustedhosts.ps1이 실패한 단계마다 돌려주는 종료 코드(템플릿 맨 위 주석과 같다).
_FAILED_STEPS = {2: "서버 목록 읽기", 3: "WinRM 서비스 시작", 4: "TrustedHosts 읽기", 5: "TrustedHosts 쓰기"}


def parse_entries(value: str | None) -> list[str]:
    return [item.strip().lower() for item in (value or "").split(",") if item.strip()]


def is_trusted(host: str, entries: Iterable[str]) -> bool:
    name = host.strip().lower()
    for entry in entries:
        if entry in ("*", name):
            return True
        if "*" in entry and fnmatch.fnmatchcase(name, entry):
            return True
    return False


def missing_hosts(hosts: Iterable[str], entries: Iterable[str]) -> list[str]:
    known = list(entries)
    return [host for host in dict.fromkeys(hosts) if not is_trusted(host, known)]


def _registry_value(key_path: str, name: str) -> object:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            value, _kind = winreg.QueryValueEx(key, name)
            return value
    except FileNotFoundError:
        return _MISSING


def read_trusted_hosts(reader: Callable[[str, str], object] = _registry_value) -> list[str] | None:
    """TrustedHosts 항목 목록. 설정한 적 없으면 []. 읽을 수 없으면 None(그때 hq는 사전 확인을 건너뛴다)."""
    try:
        policy = reader(POLICY_KEY, POLICY_VALUE)
        if policy is not _MISSING:
            return parse_entries(str(policy))
        value = reader(CLIENT_KEY, CLIENT_VALUE)
    except OSError:
        return None
    return [] if value is _MISSING else parse_entries(str(value))


def template_text() -> str:
    return resource("ps/add-trustedhosts.ps1").read_text(encoding="utf-8-sig")


def build_add_script(hosts: Iterable[str]) -> str:
    """서버 목록은 ps_json_literal로만 넣는다. 스크립트는 ConvertFrom-Json으로 읽는다(Base64는 V3가 막는다)."""
    cleaned = [validate_host(h) for h in hosts]
    return template_text().replace(_PLACEHOLDER, ps_json_literal(cleaned))


class AddResult(StrEnum):
    OK = "ok"
    CANCELLED = "cancelled"
    FAILED = "failed"


def add_trusted_hosts(hosts: Iterable[str], run: Callable[[str, str], int | None] | None = None) -> AddResult:
    wanted = list(dict.fromkeys(hosts))
    if not wanted:
        return AddResult.OK
    argv = encoded_argv(build_add_script(wanted), no_exit=False)
    code = (run or run_elevated)(argv[0], subprocess.list2cmdline(argv[1:]))
    if code is None:
        return AddResult.CANCELLED
    if code == 0:
        return AddResult.OK
    _log.warning("TrustedHosts 추가 실패: 종료 코드 %s (%s)", code, _FAILED_STEPS.get(code, "알 수 없는 단계"))
    return AddResult.FAILED


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_SW_HIDE = 0
_ERROR_CANCELLED = 1223
_WAIT_MS = 120_000


def run_elevated(file: str, parameters: str) -> int | None:
    """UAC 확인을 받아 관리자 권한으로 실행하고 종료 코드를 돌려준다. 사용자가 취소하면 None."""
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = _SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = file
    info.lpParameters = parameters
    info.nShow = _SW_HIDE
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == _ERROR_CANCELLED:
            return None
        raise ctypes.WinError(error)
    try:
        kernel32.WaitForSingleObject(info.hProcess, _WAIT_MS)
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value
    finally:
        kernel32.CloseHandle(info.hProcess)
