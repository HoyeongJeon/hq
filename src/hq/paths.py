"""hq가 쓰는 폴더 위치. 개발 중과 PyInstaller로 묶은 exe 안에서 모두 맞는 경로를 준다."""

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    """%LOCALAPPDATA%\\hq. 서버 목록, 비밀번호 파일, 로그가 모두 여기에만 있다."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise RuntimeError("LOCALAPPDATA 환경 변수가 없습니다.")
    path = Path(base) / "hq"
    path.mkdir(parents=True, exist_ok=True)
    return path


def package_dir() -> Path:
    """hq 패키지 폴더. exe 안에서는 PyInstaller가 풀어 둔 임시 폴더 아래의 hq/."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root) / "hq"
    return Path(__file__).resolve().parent


def resource(relative: str) -> Path:
    return package_dir() / relative


def index_url() -> str:
    """화면 파일의 file:// 주소. http 주소가 아니어서 pywebview가 내부 웹 서버(포트)를 띄우지 않는다."""
    return resource("static/index.html").as_uri()
