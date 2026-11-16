"""python -m hq.mcp. 인자가 없으면 MCP 서버(Claude Code가 띄운다), 있으면 서버 사용 설정 명령줄(사람이 쓴다, 스펙 6장).

어느 쪽이든 LOCALAPPDATA가 없거나 파일을 읽거나 쓰지 못하면 트레이스백 대신 한국어 한 줄을 표준 오류에 쓰고 FAILED로
끝난다. 명령줄의 표준 출력을 파이프나 파일로 돌리면 파이썬은 Windows 코드 페이지(cp949)로 쓴다. 그 코드 페이지에
없는 글자(별명의 이모지 등)는 \\U0001f600처럼 적어 멈추지 않게 한다. 표준 오류는 원래 그렇게 쓴다. 서버의 프로토콜은
바이트로 쓰므로 코드 페이지와 상관없다.
"""

import sys
from collections.abc import Sequence

from hq.mcp import cli, server
from hq.mcp.access import AccessError
from hq.mcp.server import failed
from hq.paths import data_dir

COMMAND_FAILED = "명령을 끝내지"


def main(argv: Sequence[str]) -> int:
    try:
        if not argv:
            return server.main()
        return _command_line(argv)
    except (OSError, AccessError) as exc:
        return failed(COMMAND_FAILED if argv else server.SERVER_FAILED, exc)


def _command_line(argv: Sequence[str]) -> int:
    sys.stdout.reconfigure(errors="backslashreplace")
    try:
        data = data_dir()
    except RuntimeError as exc:  # LOCALAPPDATA가 없다
        return failed(COMMAND_FAILED, exc)
    return cli.main(argv, data=data, out=sys.stdout)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
