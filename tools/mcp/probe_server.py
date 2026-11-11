"""체크포인트 A(스펙 15장)에서 Claude Code의 확인 창을 보려고 등록하는 가짜 MCP 서버.

서버에 접속하는 코드가 없다. 진짜와 이름, 입력 모양, 힌트, _meta가 같은 세 기능을 내놓고 받은 인자를 그대로 돌려준다.
setup-probe.ps1이 C:\\dev\\hq-mcp-probe에만 등록하고 remove-probe.ps1이 지운다.
"""

import json
import sys
import threading
from datetime import datetime
from pathlib import Path

from hq.mcp.protocol import Handler, Server, Tool, ToolResult
from hq.mcp.schema import LIST_SERVERS, RUN_QUERY, RUN_SCRIPT, tool_shapes

DESCRIPTION = "체크포인트 A용 가짜"
SERVERS = {"servers": [{"target": "시험01 (192.0.2.10)", "host": "192.0.2.10"}]}
SLOW_SECONDS = 130  # 2분 넘게 기다렸다 돌려준 결과도 통째로 가는지 본다
# 결과 크기 한도(스펙 9장: 30,000, 한글 한 글자는 4)에 가까운 한글 결과. 끝 문구로 잘리지 않았는지 알아본다
BIG_TEXT = "가나다라마바사아자차" * 739 + "끝까지모두받았습니다"
LOG_FILE = Path("probe-log.txt")  # Claude Code가 이 서버를 띄운 작업 폴더에 쓴다

_log_lock = threading.Lock()  # 겹친 호출의 기록 줄이 섞이지 않게 한다


def build() -> tuple[list[Tool], dict[str, Handler]]:
    shapes = tool_shapes()
    tools = [
        Tool(name, DESCRIPTION, shapes[name]["input_schema"], shapes[name]["annotations"], shapes[name]["meta"])
        for name in (LIST_SERVERS, RUN_QUERY, RUN_SCRIPT)
    ]
    return tools, {LIST_SERVERS: _list_servers, RUN_QUERY: _echo, RUN_SCRIPT: _echo}


def _list_servers(arguments: dict, cancel: threading.Event) -> ToolResult:
    return ToolResult(json.dumps(SERVERS, ensure_ascii=False))


def _echo(arguments: dict, cancel: threading.Event) -> ToolResult:
    script = arguments.get("script")
    if script == "big":
        return ToolResult(BIG_TEXT)
    if script == "big-slow":
        cancel.wait(SLOW_SECONDS)  # 취소 신호가 켜지면 바로 멈춘다
        return ToolResult(BIG_TEXT)
    return ToolResult(json.dumps(arguments, ensure_ascii=False))


def _write_log(text: str) -> None:
    with _log_lock, LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(f"{datetime.now().isoformat(timespec='seconds')} {text}\n")


def _logged(name: str, handler: Handler) -> Handler:
    def run(arguments: dict, cancel: threading.Event) -> ToolResult:
        # 문자열 인자는 그 글자 수, 그 밖의 인자는 JSON으로 쓴 글자 수. 20,000자 스크립트가 다 왔는지 본다
        sizes = {
            key: len(value) if isinstance(value, str) else len(json.dumps(value, ensure_ascii=False))
            for key, value in arguments.items()
        }
        _write_log(f"{name} {json.dumps(sizes)}")
        return handler(arguments, cancel)

    return run


class _ProbeServer(Server):
    def _initialize(self, params: dict) -> dict:
        _write_log(f"initialize protocolVersion={params.get('protocolVersion')}")
        return super()._initialize(params)


def main() -> None:
    tools, handlers = build()
    logged = {name: _logged(name, handler) for name, handler in handlers.items()}
    _ProbeServer(tools, logged, version="probe", reader=sys.stdin.buffer, writer=sys.stdout.buffer).serve()


if __name__ == "__main__":
    main()
