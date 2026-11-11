import io
import json
import os
import threading
from contextlib import contextmanager

from hq.mcp.protocol import PROTOCOL_VERSIONS, Server, Tool, ToolResult

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
}
ECHO = Tool("echo", "설명", {"type": "object"}, {"readOnlyHint": True})
ASKS = Tool("asks", "설명", {"type": "object"}, {"destructiveHint": True}, {"anthropic/requiresUserInteraction": True})
CALL = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "echo", "arguments": {}}}


def _line(message) -> bytes:
    if isinstance(message, bytes):
        return message
    return json.dumps(message, ensure_ascii=False).encode() + b"\n"


def _parse(raw: bytes) -> list[dict]:
    return [json.loads(line) for line in raw.splitlines()]


def run(messages, tools=(), handlers=None) -> bytes:
    reader = io.BytesIO(b"".join(_line(message) for message in messages))
    writer = io.BytesIO()
    Server(tools, handlers or {}, version="0.0", reader=reader, writer=writer).serve()
    return writer.getvalue()


def talk(messages, tools=(), handlers=None) -> list[dict]:
    return _parse(run(messages, tools, handlers))


@contextmanager
def piped(tools, handlers):
    """os.pipe로 입력을 주며 serve()를 다른 스레드에서 돌린다. (보내기, 입력 닫기, 출력)을 준다."""
    read_fd, write_fd = os.pipe()
    output = io.BytesIO()
    with open(read_fd, "rb") as reader, open(write_fd, "wb") as pipe_input:
        server = Server(tools, handlers, version="0.0", reader=reader, writer=output)
        thread = threading.Thread(target=server.serve, daemon=True)
        thread.start()

        def send(message) -> None:
            pipe_input.write(_line(message))
            pipe_input.flush()

        def close_input(timeout: float = 5) -> bool:
            """입력을 닫고 serve()가 timeout 안에 돌아왔는지 알려 준다."""
            pipe_input.close()
            thread.join(timeout)
            return not thread.is_alive()

        yield send, close_input, output


def test_initialize_answers_with_the_asked_version_when_it_is_known():
    result = talk([INIT])[0]["result"]
    assert result == {
        "protocolVersion": "2025-06-18",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "hq", "version": "0.0"},
    }


def test_initialize_falls_back_to_the_newest_known_version():
    asked = {**INIT, "params": {"protocolVersion": "1999-01-01", "capabilities": {}}}
    assert talk([asked])[0]["result"]["protocolVersion"] == PROTOCOL_VERSIONS[0]


def test_tools_list_puts_meta_only_on_the_tool_that_has_it():
    tools = talk([{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}], tools=(ECHO, ASKS))[0]["result"]["tools"]
    assert tools[0] == {
        "name": "echo",
        "description": "설명",
        "inputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True},
    }
    assert tools[1]["_meta"] == {"anthropic/requiresUserInteraction": True}


def test_a_call_returns_the_handlers_text():
    call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "echo", "arguments": {"a": "한글"}}}
    out = talk([call], tools=(ECHO,), handlers={"echo": lambda args, cancel: ToolResult(args["a"])})
    assert out == [
        {"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text", "text": "한글"}], "isError": False}}
    ]


def test_a_handler_that_raises_becomes_a_fixed_error_text(caplog):
    def broken(args, cancel):
        raise RuntimeError("처리기 고장")

    result = talk([CALL], tools=(ECHO,), handlers={"echo": broken})[0]["result"]
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert text == "내부 오류가 났습니다. logs\\hq-mcp.log를 보세요."
    assert "Traceback" not in text
    assert "처리기 고장" in caplog.text  # 예외는 Claude에게 보내지 않고 로그에만 남긴다


def test_unknown_method_unknown_tool_and_unknown_notification():
    out = talk(
        [
            {"jsonrpc": "2.0", "id": 4, "method": "resources/list"},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "nope", "arguments": {}}},
            {"jsonrpc": "2.0", "method": "notifications/nothing"},
        ],
        tools=(ECHO,),
        handlers={"echo": lambda args, cancel: ToolResult("")},
    )
    assert [message["id"] for message in out] == [4, 5]
    assert [message["error"]["code"] for message in out] == [-32601, -32602]


def test_a_broken_line_gets_a_parse_error_and_the_next_request_still_works():
    out = talk([b"{not json\n", {"jsonrpc": "2.0", "id": 9, "method": "ping"}])
    assert out[0] == {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": out[0]["error"]["message"]}}
    assert out[1] == {"jsonrpc": "2.0", "id": 9, "result": {}}


def test_twenty_overlapping_calls_never_mix_their_lines():
    together = threading.Barrier(20)
    text = "가" * 5000

    def handler(args, cancel):
        together.wait(5)  # 스무 호출이 모두 동시에 돌아야 지나간다
        return ToolResult(text)

    calls = [{**CALL, "id": number} for number in range(20)]
    out = talk(calls, tools=(ECHO,), handlers={"echo": handler})
    assert sorted(message["id"] for message in out) == list(range(20))
    assert all(message["result"] == {"content": [{"type": "text", "text": text}], "isError": False} for message in out)


def test_a_cancelled_call_sends_no_response():
    woke = threading.Event()

    def handler(args, cancel):
        if cancel.wait(5):
            woke.set()
        return ToolResult("늦은 결과")

    with piped((ECHO,), {"echo": handler}) as (send, close_input, output):
        send({**CALL, "id": 10})
        send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 10, "reason": "멈춤"}})
        send({"jsonrpc": "2.0", "id": 11, "method": "ping"})
        assert woke.wait(2)  # 입력을 닫기 전에 취소 알림만으로 처리기가 깨어난다
        assert close_input()
    assert _parse(output.getvalue()) == [{"jsonrpc": "2.0", "id": 11, "result": {}}]


def test_closing_the_input_cancels_running_calls_and_serve_returns():
    started = threading.Event()

    def handler(args, cancel):
        started.set()
        cancel.wait(10)
        return ToolResult("stopped")

    with piped((ECHO,), {"echo": handler}) as (send, close_input, output):
        send(CALL)
        assert started.wait(2)
        assert close_input(timeout=2)
    assert _parse(output.getvalue()) == [
        {"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text", "text": "stopped"}], "isError": False}}
    ]


def test_output_is_utf8_with_raw_hangul_one_message_per_line():
    call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "echo", "arguments": {"a": "한글"}}}
    raw = run([INIT, call], tools=(ECHO,), handlers={"echo": lambda args, cancel: ToolResult(args["a"])})
    assert "한글".encode() in raw
    assert b"\\u" not in raw
    lines = raw.split(b"\n")
    assert lines[-1] == b""
    assert [json.loads(line)["id"] for line in lines[:-1]] == [1, 3]
