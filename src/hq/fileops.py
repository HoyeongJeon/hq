"""HQ 창과 MCP 서버가 같은 파일을 함께 쓸 때의 짧은 재시도.

한 프로세스가 파일을 읽는 순간 다른 프로세스가 os.replace로 바꿔치기하면 WinError 5(PermissionError)로 실패한다.
읽기는 곧 끝나므로 잠깐 기다렸다가 다시 한다(스펙 3장). 파일이 없는 것처럼 기다려도 나아지지 않는 오류는 바로 던진다.
"""

import os
import time
from collections.abc import Callable
from pathlib import Path


def _retry[T](action: Callable[[], T], attempts: int, delay: float) -> T:
    """action을 PermissionError일 때만 attempts번까지 부른다. 마지막 시도의 예외는 그대로 던진다."""
    for _ in range(attempts - 1):
        try:
            return action()
        except PermissionError:
            time.sleep(delay)
    return action()


def replace_with_retry(src: Path, dst: Path, *, attempts: int = 5, delay: float = 0.05) -> None:
    _retry(lambda: os.replace(src, dst), attempts, delay)


def read_text_with_retry(path: Path, *, encoding: str = "utf-8", attempts: int = 5, delay: float = 0.05) -> str:
    return _retry(lambda: path.read_text(encoding=encoding), attempts, delay)
