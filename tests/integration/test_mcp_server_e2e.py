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
    assert "Get-Date" not in (logs / "hq-mcp.log").read_text(encoding="utf-8")  # 스크립트는 로그에 쓰지 않는다


def test_arguments_run_the_command_line_instead(tmp_path):
    inventory(tmp_path, [("웹01", "", "192.0.2.10")])
    result = command_line(tmp_path, "list")
    text = result.stdout.decode(locale.getencoding())
    assert result.returncode == 0, result.stderr
    assert "꺼짐" in text and "웹01 (192.0.2.10)" in text
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)


def test_the_command_line_prints_a_name_its_code_page_lacks(tmp_path):
    inventory(tmp_path, [("웹01", f"웹{GRINNING}", "192.0.2.10")])
    result = command_line(tmp_path, "list")
    assert result.returncode == 0 and b"Traceback" not in result.stderr, result.stderr
    assert f"웹{GRINNING} (192.0.2.10)".encode(locale.getencoding(), "backslashreplace") in result.stdout
