"""표준 입출력으로 MCP(JSON-RPC 2.0)를 주고받는다. 메시지는 한 줄에 하나, UTF-8이다."""

import json
import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import BinaryIO

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")  # 앞이 최신

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

INTERNAL_ERROR_TEXT = "내부 오류가 났습니다. logs\\hq-mcp.log를 보세요."

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    annotations: dict
    meta: dict | None = None


@dataclass(frozen=True)
class ToolResult:
    text: str
    is_error: bool = False


Handler = Callable[[dict, threading.Event], ToolResult]  # (arguments, 취소 신호)


@dataclass(eq=False)
class _Call:
    request_id: object
    cancel: threading.Event = field(default_factory=threading.Event)
    dropped: bool = False  # notifications/cancelled로 취소됐다. 처리기가 끝나도 응답을 쓰지 않는다


class Server:
    def __init__(
        self,
        tools: Sequence[Tool],
        handlers: Mapping[str, Handler],
        *,
        version: str,
        reader: BinaryIO,
        writer: BinaryIO,
        name: str = "hq",
    ) -> None:
        self._tools = tuple(tools)
        self._handlers = dict(handlers)
        self._version = version
        self._reader = reader
        self._writer = writer
        self._name = name
        self._write_lock = threading.Lock()  # 겹친 호출의 응답 줄이 섞이지 않게 쓰는 곳을 하나로 모은다
        self._calls_lock = threading.Lock()  # _calls와 그 안의 _Call.dropped를 지킨다
        self._calls: dict[_Call, threading.Thread] = {}  # 응답을 다 쓰기 전의 호출

    def serve(self) -> None:
        """입력이 끝날 때까지 메시지를 처리한다. 끝나면 모든 취소 신호를 켜고 돌던 호출을 기다린다."""
        try:
            for line in self._reader:
                self._receive(line)
        finally:
            self._finish_calls()

    def _receive(self, line: bytes) -> None:
        try:
            message = json.loads(line)
        except ValueError:
            self._send_error(None, PARSE_ERROR, "JSON으로 읽을 수 없는 줄입니다.")
            return
        if not isinstance(message, dict):
            self._send_error(None, INVALID_REQUEST, "메시지는 JSON 객체여야 합니다.")
            return
        method = message.get("method")
        params = message.get("params")
        if not isinstance(params, dict):
            params = {}
        if "id" not in message:  # 알림에는 답하지 않는다. 모르는 알림은 무시한다
            if method == "notifications/cancelled":
                self._cancel(params.get("requestId"))
            return
        request_id = message["id"]
        if method == "initialize":
            self._send_result(request_id, self._initialize(params))
        elif method == "ping":
            self._send_result(request_id, {})
        elif method == "tools/list":
            self._send_result(request_id, {"tools": [_describe(tool) for tool in self._tools]})
        elif method == "tools/call":
            self._start_call(request_id, params)
        else:
            self._send_error(request_id, METHOD_NOT_FOUND, f"모르는 메서드입니다: {method}")

    def _initialize(self, params: dict) -> dict:
        asked = params.get("protocolVersion")
        return {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": self._name, "version": self._version},
        }

    def _start_call(self, request_id: object, params: dict) -> None:
        name = params.get("name")
        handler = self._handlers.get(name) if isinstance(name, str) else None
        if handler is None:
            self._send_error(request_id, INVALID_PARAMS, f"모르는 기능입니다: {name}")
            return
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            self._send_error(request_id, INVALID_PARAMS, "arguments는 JSON 객체여야 합니다.")
            return
        call = _Call(request_id)
        # 호출마다 스레드를 따로 쓴다. Claude Code는 readOnlyHint 기능을 동시에 부를 수 있다
        thread = threading.Thread(target=self._run, args=(call, name, handler, arguments), name=f"hq-mcp-{name}")
        with self._calls_lock:
            self._calls[call] = thread
        thread.start()

    def _run(self, call: _Call, name: str, handler: Handler, arguments: dict) -> None:
        try:
            result = handler(arguments, call.cancel)
        except Exception:
            # 예외 내용은 Claude에게 보내지 않는다. 경로 같은 정보가 섞일 수 있다
            log.exception("기능 %s의 처리기에서 예외가 났습니다.", name)
            result = ToolResult(INTERNAL_ERROR_TEXT, is_error=True)
        with self._calls_lock:
            dropped = call.dropped
        if not dropped:
            content = [{"type": "text", "text": result.text}]
            self._send_result(call.request_id, {"content": content, "isError": result.is_error})
        with self._calls_lock:
            del self._calls[call]

    def _cancel(self, request_id: object) -> None:
        with self._calls_lock:
            for call in self._calls:
                if call.request_id == request_id:
                    call.dropped = True
                    call.cancel.set()

    def _finish_calls(self) -> None:
        with self._calls_lock:
            running = list(self._calls.items())
        for call, _ in running:
            call.cancel.set()
        for _, thread in running:
            thread.join()

    def _send_result(self, request_id: object, result: dict) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _send_error(self, request_id: object, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})

    def _send(self, message: dict) -> None:
        # 짝 없는 서로게이트가 들어와도 UTF-8로 쓸 수 있게 바꾼다
        data = json.dumps(message, ensure_ascii=False).encode("utf-8", "replace") + b"\n"
        with self._write_lock:
            try:
                self._writer.write(data)
                self._writer.flush()
            except OSError:
                log.warning("출력이 닫혀 응답을 버렸습니다.")


def _describe(tool: Tool) -> dict:
    entry = {
        "name": tool.name,
        "description": tool.description,
        "inputSchema": tool.input_schema,
        "annotations": tool.annotations,
    }
    if tool.meta is not None:
        entry["_meta"] = tool.meta
    return entry
