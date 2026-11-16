"""MCP 서버의 입구. Claude Code가 python -m hq.mcp를 자식 프로세스로 띄우면 여기서 시작한다(스펙 3장).

진짜 표준 출력에는 MCP 메시지만 쓴다. 그래서 맨 먼저 그 바이트 스트림을 따로 쥐고 sys.stdout을 표준 오류로 바꾼다.
실수로 print해도 프로토콜이 깨지지 않는다. 프로토콜은 바이트를 UTF-8로 주고받으므로 콘솔 코드 페이지(cp949)와 상관없다.
파이프로 이어진 표준 오류도 무엇을 쓰기 전에 UTF-8로 바꾼다. 파이썬의 기본은 cp949라 그 밖의 글자가 \\U0001f600처럼
된다.
로그는 logs\\hq-mcp.log와 표준 오류로 보낸다. 창의 hq.log(configure_logging)는 쓰지 않는다. 창과 동시에 돌 때 로그
교체가 파일 잠금에 걸린다. 스크립트와 서버 출력은 로그에 쓰지 않는다.
"""

import logging
import logging.handlers
import sys
from pathlib import Path

import hq
from hq.mcp.access import resolve_host
from hq.mcp.audit import AuditLog
from hq.mcp.protocol import Server
from hq.mcp.querycheck import check_query, load_rules
from hq.mcp.runner import run_on_server
from hq.mcp.tools import Deps, build_handlers, build_tools
from hq.paths import data_dir
from hq.probe import probe_all
from hq.trustedhosts import read_trusted_hosts

LOG_FILE = "hq-mcp.log"
FAILED = 1  # 시작하지 못했다(서버), 파일을 읽거나 쓰지 못했다(명령줄). 명령줄의 0(성공), 2(거절)와 다르다
SERVER_FAILED = "MCP 서버를 시작하지"
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class LogFileHandler(logging.handlers.RotatingFileHandler):
    """크기가 차면 바꿔치기하는 로그 파일. 바꿔치기를 하지 못하면 줄을 버리지 않고 같은 파일에 이어 쓴다.

    Claude Code 세션 둘이 띄운 hq.mcp, 또는 다시 연결하는 동안 남은 예전 프로세스가 같은 hq-mcp.log를 열고 있으면
    Windows는 이름 바꾸기를 막는다(WinError 32). 그때 파일은 maxBytes를 넘어 자라고, 다음 줄마다 다시 바꿔 보다가 다른
    프로세스가 파일을 닫은 뒤에 바꾼다.
    """

    def doRollover(self) -> None:  # noqa: N802 (logging의 이름)
        try:
            super().doRollover()
        except OSError:
            if self.stream is None:  # 부모가 바꾸기 전에 닫았다
                self.stream = self._open()


def main() -> int:
    """입력이 끝날 때까지 MCP 메시지를 처리하고 0을 돌려준다.

    LOCALAPPDATA가 없으면 한 줄을 표준 오류에 쓰고 FAILED를 돌려준다. 로그 파일을 열지 못하면 OSError를 던진다.
    """
    protocol_out = sys.stdout.buffer
    if not sys.stderr.isatty():
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stdout = sys.stderr
    try:
        data = data_dir()
    except RuntimeError as exc:  # LOCALAPPDATA가 없다
        return failed(SERVER_FAILED, exc)
    handlers = _start_logging(data / "logs")
    try:
        deps = Deps(
            data=data,
            resolve=resolve_host,
            probe=probe_all,
            read_trusted=read_trusted_hosts,
            check=check_query,
            execute=run_on_server,
            audit=AuditLog(data / "logs"),  # 프로세스에 하나. 겹친 호출이 함께 쓴다
        )
        Server(
            build_tools(load_rules()),
            build_handlers(deps),
            version=hq.__version__,
            reader=sys.stdin.buffer,
            writer=protocol_out,
        ).serve()
    finally:
        _stop_logging(handlers)
    return 0


def failed(what: str, exc: Exception) -> int:
    """'<what> 못했습니다: <이유>' 한 줄을 표준 오류에 쓰고 FAILED를 돌려준다. 트레이스백은 쓰지 않는다."""
    print(f"{what} 못했습니다: {exc}", file=sys.stderr)
    return FAILED


def _start_logging(directory: Path) -> list[logging.Handler]:
    """로그 파일과 표준 오류에 쓰는 처리기를 root 로거에 단다. 로그 파일을 열지 못하면 아무것도 달지 않고 던진다."""
    directory.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        LogFileHandler(directory / LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ]
    root = logging.getLogger()
    for handler in handlers:
        handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(handler)
    return handlers


def _stop_logging(handlers: list[logging.Handler]) -> None:
    root = logging.getLogger()
    for handler in handlers:
        root.removeHandler(handler)
        handler.close()
