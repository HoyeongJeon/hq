"""python -m hq.mcp를 임시 LOCALAPPDATA로 띄워 파이프로 주고받는다(스펙 15장 통합의 마지막 항목).

어느 호출도 서버에 접속하지 않는다. 사용 설정 파일이 없으므로 run_query는 어느 서버에도 접속하기 전에 거절된다.
자식 파이썬은 PYTHONIOENCODING과 PYTHONUTF8 없이 띄운다. 그러면 파이프로 돌린 표준 출력과 오류가 Windows의 기본
코드 페이지(한국어 Windows는 cp949)를 쓴다. 그래도 프로토콜은 UTF-8이고, 사람이 읽는 출력은 그 코드 페이지에 없는
글자에서 멈추지 않아야 한다.
"""

import json
import locale
import os
import subprocess
import sys
from subprocess import PIPE

import pytest

from hq.inventory import Inventory

pytestmark = pytest.mark.integration

GRINNING = "\N{GRINNING FACE}"  # cp949에 없는 글자
INIT_PARAMS = {"protocolVersion": "2025-06-18", "capabilities": {}}


def environment(tmp_path):
    env = {**os.environ, "LOCALAPPDATA": str(tmp_path)}
    for name in ("PYTHONIOENCODING", "PYTHONUTF8"):
        env.pop(name, None)
    return env


def send(proc, message):
    proc.stdin.write(json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n")
    proc.stdin.flush()


def call(request_id, name, arguments):
    params = {"name": name, "arguments": arguments}
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": params}


def inventory(tmp_path, servers):
    """(이름, 별명, host)들을 임시 LOCALAPPDATA의 hq\\inventory.json에 쓴다."""
    inv = Inventory(tmp_path / "hq" / "inventory.json")
    for name, nickname, host in servers:
        inv.add({"name": name, "nickname": nickname, "host": host, "username": "admin"})
    inv.save()


def write_inventory(tmp_path, items):
    """HQ가 받지 않을 항목도 그대로 쓴다(손으로 고친 파일)."""
    (tmp_path / "hq").mkdir(exist_ok=True)
    data = {"version": 1, "settings": {"admin_ips": []}, "servers": items}
    (tmp_path / "hq" / "inventory.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def mcp(tmp_path, messages, *, env=None):
    """messages를 표준 입력으로 주고 닫은 뒤 서버가 끝날 때까지 기다린다."""
    stdin = b"".join(json.dumps(m, ensure_ascii=False).encode("utf-8") + b"\n" for m in messages)
    return subprocess.run(
        [sys.executable, "-m", "hq.mcp"],
        input=stdin,
        capture_output=True,
        env=env or environment(tmp_path),
        timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def command_line(tmp_path, *argv):
    return subprocess.run(
        [sys.executable, "-m", "hq.mcp", *argv],
        capture_output=True,
        env=environment(tmp_path),
        timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def test_the_real_entry_point_speaks_only_mcp_on_stdout_and_ends_when_stdin_closes(tmp_path):
    proc = subprocess.Popen(
        [sys.executable, "-m", "hq.mcp"],
        stdin=PIPE,
        stdout=PIPE,
        stderr=PIPE,
        env=environment(tmp_path),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": INIT_PARAMS})
        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        send(proc, call(3, "list_servers", {}))
        send(proc, call(4, "run_query", {"targets": ["없음 (10.0.1.21)"], "script": "Get-Date"}))
        first = [json.loads(proc.stdout.readline()) for _ in range(4)]  # 응답 넷을 받은 뒤에 입력을 닫는다
        rest, errors = proc.communicate(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    assert proc.returncode == 0 and rest == b"", errors  # 닫으면 끝나고, 더 쓴 것이 없다
    by_id = {m["id"]: m for m in first}  # 호출은 스레드에서 돌아 순서가 바뀔 수 있다
    assert sorted(by_id) == [1, 2, 3, 4]
    assert by_id[1]["result"]["serverInfo"]["name"] == "hq"
    tools = {t["name"]: t for t in by_id[2]["result"]["tools"]}
    assert tools["run_script"]["_meta"] == {"anthropic/requiresUserInteraction": True}
    assert "_meta" not in tools["run_query"] and "_meta" not in tools["list_servers"]
    assert all(len(t["description"]) <= 4096 for t in tools.values())
    assert json.loads(by_id[3]["result"]["content"][0]["text"])["servers"] == []
    assert by_id[4]["result"]["isError"] is True
    logs = tmp_path / "hq" / "logs"
    assert (logs / "hq-mcp.log").exists() and not (logs / "hq.log").exists()


def test_the_server_writes_stderr_in_utf8_even_through_a_pipe(tmp_path):
    # 서버 목록의 host에 cp949에 없는 글자가 있어 목록을 읽지 못한다. 그 경고가 표준 오류에 UTF-8로 그대로 간다
    write_inventory(tmp_path, [{"id": "0" * 32, "name": "웹01", "host": f"웹{GRINNING}", "username": "admin"}])
    (tmp_path / "hq" / "mcp-access.json").write_text('{"version": 1, "enabled": [], "never": []}', encoding="utf-8")
    messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": INIT_PARAMS}, call(2, "list_servers", {})]
    result = mcp(tmp_path, messages)
    assert result.returncode == 0 and len(result.stdout.splitlines()) == 2
    assert f"웹{GRINNING}" in result.stderr.decode("utf-8")


def test_the_server_says_in_one_utf8_line_that_localappdata_is_missing(tmp_path):
    env = environment(tmp_path)
    del env["LOCALAPPDATA"]
    result = mcp(tmp_path, [], env=env)
    assert result.returncode == 1 and result.stdout == b""
    expected = "MCP 서버를 시작하지 못했습니다: LOCALAPPDATA 환경 변수가 없습니다."
    assert result.stderr.decode("utf-8").splitlines() == [expected]


def test_arguments_run_the_command_line_instead(tmp_path):
    inventory(tmp_path, [("웹01", "", "192.0.2.10")])
    result = command_line(tmp_path, "list")
    text = result.stdout.decode(locale.getencoding())
    assert result.returncode == 0, result.stderr
    assert "꺼짐" in text and "웹01 (192.0.2.10)" in text
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)


def test_the_command_line_never_prints_an_escape_sequence_from_the_server_list(tmp_path):
    # 손으로 고친 host가 목록을 읽지 못한 이유에 그대로 든다. 명령줄은 그 이유를 표준 출력에 \x1b처럼 적어 찍는다
    write_inventory(tmp_path, [{"id": "0" * 32, "name": "웹01", "host": "web\x1b[2J01", "username": "admin"}])
    result = command_line(tmp_path, "list")
    assert result.returncode == 2 and b"Traceback" not in result.stderr
    assert b"\x1b" not in result.stdout + result.stderr and b"web\\x1b[2J01" in result.stdout


def test_the_command_line_prints_a_name_its_code_page_lacks(tmp_path):
    inventory(tmp_path, [("웹01", f"웹{GRINNING}", "192.0.2.10")])
    result = command_line(tmp_path, "list")
    assert result.returncode == 0 and b"Traceback" not in result.stderr, result.stderr
    assert f"웹{GRINNING} (192.0.2.10)".encode(locale.getencoding(), "backslashreplace") in result.stdout
