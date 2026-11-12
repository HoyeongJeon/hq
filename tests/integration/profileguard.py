"""통합 테스트용: 대화형 셸을 띄우기 전에, 시험이 사용자의 실제 프로필로 돌고 있지 않은지 본다.

대화형 셸(ConPTY)의 PSReadLine은 친 줄을 프로필 아래의 입력 기록(ConsoleHost_history.txt)에 쓴다. conftest.py의
temp_appdata가 시험마다 USERPROFILE을 임시 폴더로 바꾸지만, tests/integration/의 파일과 tests/의 파일을 한 pytest
명령에 섞으면 pytest 9가 그 conftest를 건너뛸 수 있다(2026-11-11 이 PC에서 사용자의 기록에 네 줄이 남았다). 그때는
셸을 띄우지 않고 시험을 실패시킨다.
"""

import ctypes
import os
from ctypes import wintypes

_TOKEN_QUERY = 0x0008
_MAX_PATH_CHARS = 32768


def require_temp_profile() -> None:
    """USERPROFILE이 없거나 이 사용자의 실제 프로필 폴더면 RuntimeError. 대화형 셸을 띄우는 도우미가 맨 먼저 부른다.

    실제 프로필 폴더는 부를 때마다 Windows에 묻는다(환경 변수도, 모듈을 읽을 때 잡아 둔 값도 쓰지 않는다).
    """
    current = os.environ.get("USERPROFILE", "")
    if not current or _same_path(current, _real_profile()):
        raise RuntimeError(
            "임시 프로필 픽스처(tests/integration/conftest.py의 temp_appdata)가 돌지 않아 USERPROFILE이 사용자의 실제 "
            "프로필입니다. 이대로 대화형 셸을 띄우면 친 줄이 사용자의 PowerShell 입력 기록에 남으므로 띄우지 않습니다. "
            "tests/integration/의 파일은 tests/의 다른 파일과 섞지 말고 따로 pytest 명령으로 돌리세요."
        )


def _real_profile() -> str:
    """이 프로세스 토큰의 사용자 프로필 폴더(GetUserProfileDirectoryW). Windows가 사용자 SID로 찾아 준다."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    userenv.GetUserProfileDirectoryW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    userenv.GetUserProfileDirectoryW.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD(_MAX_PATH_CHARS)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not userenv.GetUserProfileDirectoryW(token, buffer, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return buffer.value
    finally:
        kernel32.CloseHandle(token)


def _same_path(a: str, b: str) -> bool:
    r"""같은 폴더인지. 둘 다 있으면 파일 시스템에 묻는다(os.path.samefile: 대소문자, 구분자, \\?\ 같은 표기와 상관없다).
    하나라도 없으면 앞의 \\?\를 떼고 대소문자, 구분자(/, \), 끝의 구분자, 짧은 이름과 연결을 풀어 견준다."""
    if os.path.exists(a) and os.path.exists(b):
        return os.path.samefile(a, b)
    return _normalised(a) == _normalised(b)


def _normalised(path: str) -> str:
    path = path.removeprefix("\\\\?\\")
    return os.path.normcase(os.path.realpath(path))
