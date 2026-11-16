"""MCP 서버 입구(server.main)와 python -m hq.mcp의 입구(__main__.main)를 이 프로세스 안에서 확인한다.

표준 입출력은 가짜로 바꾼다. 가짜 표준 출력과 오류는 파이프로 돌린 Windows 파이썬의 기본처럼 cp949다(출력은 strict,
오류는 backslashreplace). LOCALAPPDATA는 임시 폴더다. 서버에 접속하는 호출은 없다.
"""

import io
import json
import logging
import sys
from dataclasses import dataclass

import hq
from hq.inventory import Inventory
from hq.mcp import __main__ as entry
from hq.mcp import cli, server
from hq.mcp.access import Access, AccessError, resolve_host, save_access
from hq.mcp.audit import AuditLog
from hq.mcp.protocol import ToolResult
from hq.mcp.querycheck import check_query
from hq.mcp.runner import run_on_server
from hq.probe import probe_all
from hq.trustedhosts import read_trusted_hosts

GRINNING = "\N{GRINNING FACE}"  # cp949에 없는 글자
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
}


def call(request_id, name, arguments=None):
    params = {"name": name, "arguments": arguments or {}}
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": params}


@dataclass
class Ran:
    code: int
    stdout: bytes  # 진짜 표준 출력에 간 바이트
    stderr: str


def run_main(monkeypatch, tmp_path, argv, messages=()):
    """가짜 표준 입출력과 임시 LOCALAPPDATA로 python -m hq.mcp의 입구를 부른다."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    data = b"".join(json.dumps(m, ensure_ascii=False).encode("utf-8") + b"\n" for m in messages)
    stdin = io.TextIOWrapper(io.BytesIO(data), encoding="utf-8")
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp949")
    stderr = io.TextIOWrapper(io.BytesIO(), encoding="cp949", errors="backslashreplace")
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    code = entry.main(argv)
    stdout.flush()
    stderr.flush()
    return Ran(code, stdout.buffer.getvalue(), stderr.buffer.getvalue().decode("cp949"))


def answers(raw):
    """표준 출력의 모든 줄은 UTF-8 JSON-RPC 응답이다. id → 응답."""
    messages = [json.loads(line) for line in raw.splitlines()]
    assert all(message["jsonrpc"] == "2.0" for message in messages)
    return {message["id"]: message for message in messages}


def text_of(answer):
    return answer["result"]["content"][0]["text"]


def add_servers(tmp_path, servers):
    """(이름, 별명, host)들을 임시 LOCALAPPDATA의 hq\\inventory.json에 쓴다."""
    inv = Inventory(tmp_path / "hq" / "inventory.json")
    for name, nickname, host in servers:
        inv.add({"name": name, "nickname": nickname, "host": host, "username": "admin"})
    inv.save()


def enable(tmp_path, *hosts):
    assert cli.main(["on", *hosts], data=tmp_path / "hq", out=io.StringIO()) == 0


def test_the_server_answers_in_utf8_on_the_stdout_bytes_whatever_the_code_page(monkeypatch, tmp_path):
    add_servers(tmp_path, [("웹01", f"웹{GRINNING}", "192.0.2.10")])
    enable(tmp_path, "192.0.2.10")
    ran = run_main(monkeypatch, tmp_path, [], [INIT, call(2, "list_servers")])
    assert ran.code == 0
    by_id = answers(ran.stdout)
    assert by_id[1]["result"]["serverInfo"] == {"name": "hq", "version": hq.__version__}
    servers = json.loads(text_of(by_id[2]))["servers"]
    assert [s["target"] for s in servers] == [f"웹{GRINNING} (192.0.2.10)"]


def test_a_stray_print_while_serving_goes_to_stderr_not_into_the_protocol(monkeypatch, tmp_path):
    def printing(deps):
        def handler(arguments, cancel):
            print("길 잃은 글")
            return ToolResult(f"결과 {GRINNING}")

        return {"list_servers": handler}

    monkeypatch.setattr(server, "build_handlers", printing)
    ran = run_main(monkeypatch, tmp_path, [], [INIT, call(2, "list_servers")])
    assert ran.code == 0
    by_id = answers(ran.stdout)
    assert sorted(by_id) == [1, 2] and text_of(by_id[2]) == f"결과 {GRINNING}"
    assert "길 잃은 글" in ran.stderr


def test_one_process_makes_one_audit_log_and_wires_the_real_functions(monkeypatch, tmp_path):
    made, seen = [], []

    class CountedAuditLog(AuditLog):
        def __init__(self, directory, **kwargs):
            super().__init__(directory, **kwargs)
            made.append(directory)

    real_build_handlers = server.build_handlers

    def capture(deps):
        seen.append(deps)
        return real_build_handlers(deps)

    monkeypatch.setattr(server, "AuditLog", CountedAuditLog)
    monkeypatch.setattr(server, "build_handlers", capture)
    ran = run_main(monkeypatch, tmp_path, [], [INIT, call(2, "list_servers"), call(3, "list_servers")])
    assert ran.code == 0 and sorted(answers(ran.stdout)) == [1, 2, 3]
    assert made == [tmp_path / "hq" / "logs"]
    (deps,) = seen
    assert isinstance(deps.audit, CountedAuditLog) and deps.data == tmp_path / "hq"
    assert deps.resolve is resolve_host and deps.probe is probe_all and deps.read_trusted is read_trusted_hosts
    assert deps.check is check_query and deps.execute is run_on_server


def test_the_log_goes_to_hq_mcp_log_and_stderr_and_is_let_go_after_serving(monkeypatch, tmp_path):
    data = tmp_path / "hq"
    save_access(data / "mcp-access.json", Access())
    (data / "inventory.json").write_text("{깨짐", encoding="utf-8")  # 읽으면 경고 줄이 남는다
    before = list(logging.getLogger().handlers)
    script = call(3, "run_query", {"targets": ["웹01 (192.0.2.10)"], "script": "Write-Output 'SCRIPT-MARK'"})
    ran = run_main(monkeypatch, tmp_path, [], [INIT, call(2, "list_servers"), script])
    assert ran.code == 0 and sorted(answers(ran.stdout)) == [1, 2, 3]
    log = data / "logs" / "hq-mcp.log"
    text = log.read_text(encoding="utf-8")
    assert "서버 목록 파일을 읽을 수 없습니다" in text and "서버 목록 파일을 읽을 수 없습니다" in ran.stderr
    assert "SCRIPT-MARK" not in text and "SCRIPT-MARK" not in ran.stderr  # 스크립트는 로그에 쓰지 않는다
    assert logging.getLogger().handlers == before
    log.unlink()  # 서버가 로그 파일을 닫았다. 열려 있으면 Windows에서 지울 수 없다
    assert not (data / "logs" / "hq.log").exists()


def test_the_server_does_not_start_when_its_log_cannot_be_opened(monkeypatch, tmp_path):
    (tmp_path / "hq" / "logs" / "hq-mcp.log").mkdir(parents=True)  # 같은 이름의 폴더가 있어 열 수 없다
    before = list(logging.getLogger().handlers)
    ran = run_main(monkeypatch, tmp_path, [], [INIT])
    assert ran.code == entry.FAILED and ran.stdout == b""
    assert "MCP 서버를 시작하지 못했습니다" in ran.stderr and "hq-mcp.log" in ran.stderr
    assert logging.getLogger().handlers == before


def test_arguments_run_the_command_line_on_the_data_folder(monkeypatch, tmp_path):
    add_servers(tmp_path, [("웹01", "", "192.0.2.10")])
    ran = run_main(monkeypatch, tmp_path, ["list"])
    assert ran.code == 0 and ran.stderr == ""
    assert "꺼짐  웹01 (192.0.2.10)" in ran.stdout.decode("cp949")


def test_the_command_line_prints_a_name_outside_the_code_page(monkeypatch, tmp_path):
    add_servers(tmp_path, [("웹01", f"웹{GRINNING}", "192.0.2.10")])
    ran = run_main(monkeypatch, tmp_path, ["list"])
    assert ran.code == 0
    assert "웹\\U0001f600 (192.0.2.10)" in ran.stdout.decode("cp949")


def test_a_file_error_ends_the_command_line_with_a_korean_message(monkeypatch, tmp_path):
    add_servers(tmp_path, [("웹01", "", "192.0.2.10")])
    (tmp_path / "hq" / "mcp-access.json.tmp").mkdir()  # 설정을 쓸 임시 파일 자리에 폴더가 있어 쓰지 못한다
    ran = run_main(monkeypatch, tmp_path, ["on", "192.0.2.10"])
    assert ran.code == entry.FAILED and ran.stdout == b""
    assert "명령을 끝내지 못했습니다" in ran.stderr and "mcp-access.json.tmp" in ran.stderr


def test_a_settings_error_ends_the_command_line_with_a_korean_message(monkeypatch, tmp_path):
    def refuse(path, access):
        raise AccessError("사용 설정 파일을 덮어쓰지 않았습니다. (시험)")

    monkeypatch.setattr(cli, "save_access", refuse)
    add_servers(tmp_path, [("웹01", "", "192.0.2.10")])
    ran = run_main(monkeypatch, tmp_path, ["on", "192.0.2.10"])
    assert ran.code == entry.FAILED and ran.stdout == b""
    assert "명령을 끝내지 못했습니다" in ran.stderr and "사용 설정 파일을 덮어쓰지 않았습니다. (시험)" in ran.stderr
