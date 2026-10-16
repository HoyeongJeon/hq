"""서버별 비밀번호 파일. DPAPI(현재 Windows 사용자)로 암호화해 16진 문자열로 저장한다.

이 형식은 키 없이 쓴 ConvertFrom-SecureString의 출력과 같아서, 탭의 powershell.exe가
ConvertTo-SecureString으로 바로 읽는다. hq(Python)는 쓰기만 하고 풀지 않는다. 그래서
비밀번호가 명령줄·환경 변수·화면을 거치지 않는다.
"""

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from hq.validation import is_hex_id


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_BLOB_FUNC_ARGS = [
    ctypes.POINTER(_Blob),
    ctypes.c_void_p,
    ctypes.POINTER(_Blob),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(_Blob),
]
_crypt32.CryptProtectData.argtypes = _BLOB_FUNC_ARGS
_crypt32.CryptProtectData.restype = wintypes.BOOL
_crypt32.CryptUnprotectData.argtypes = _BLOB_FUNC_ARGS
_crypt32.CryptUnprotectData.restype = wintypes.BOOL
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_kernel32.LocalFree.restype = ctypes.c_void_p


def _call(func, data: bytes) -> bytes:
    buffer = ctypes.create_string_buffer(data, len(data))
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    result = _Blob()
    if not func(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def protect(plaintext: bytes) -> bytes:
    return _call(_crypt32.CryptProtectData, plaintext)


def _unprotect(blob: bytes) -> bytes:
    """테스트 확인용. 앱 흐름에서는 비밀번호를 풀지 않는다."""
    return _call(_crypt32.CryptUnprotectData, blob)


class SecretStore:
    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def path_for(self, server_id: str) -> Path:
        if not is_hex_id(server_id):
            raise ValueError(f"잘못된 서버 id: {server_id!r}")
        return self._dir / f"{server_id}.dpapi"

    def save(self, server_id: str, password: str) -> None:
        if not password:
            raise ValueError("비밀번호가 비어 있습니다.")
        path = self.path_for(server_id)
        self._dir.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(protect(password.encode("utf-16-le")).hex(), encoding="ascii")
        os.replace(temp, path)

    def has(self, server_id: str) -> bool:
        return self.path_for(server_id).is_file()

    def delete(self, server_id: str) -> None:
        self.path_for(server_id).unlink(missing_ok=True)
