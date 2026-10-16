import base64
import ctypes
import json
import logging

import pytest

from hq import trustedhosts
from hq.trustedhosts import (
    AddResult,
    add_trusted_hosts,
    build_add_script,
    is_trusted,
    missing_hosts,
    parse_entries,
    read_trusted_hosts,
)
from hq.validation import ValidationError


def test_parse_entries():
    assert parse_entries(" 10.0.1.21, WEB01 ,,") == ["10.0.1.21", "web01"]
    assert parse_entries(None) == []


@pytest.mark.parametrize(
    ("host", "entries", "expected"),
    [
        ("10.0.1.21", ["10.0.1.21"], True),
        ("WEB01", ["web01"], True),
        ("10.0.1.21", ["*"], True),
        ("web01.corp.local", ["*.corp.local"], True),
        ("10.0.1.21", ["10.0.1.*"], True),
        ("10.0.1.22", ["10.0.1.21"], False),
        ("10.0.1.21", [], False),
    ],
)
def test_is_trusted(host, entries, expected):
    assert is_trusted(host, entries) is expected


def test_missing_hosts_keeps_order_without_duplicates():
    assert missing_hosts(["10.0.1.31", "10.0.1.21", "10.0.1.31"], ["10.0.1.21"]) == ["10.0.1.31"]


def fake_reader(values):
    def reader(key, name):
        value = values.get((key, name), trustedhosts._MISSING)
        if isinstance(value, Exception):
            raise value
        return value

    return reader


def test_read_prefers_group_policy():
    reader = fake_reader({(trustedhosts.POLICY_KEY, trustedhosts.POLICY_VALUE): "a,b",
                          (trustedhosts.CLIENT_KEY, trustedhosts.CLIENT_VALUE): "c"})
    assert read_trusted_hosts(reader) == ["a", "b"]


def test_read_client_value_or_empty_when_never_set():
    client = {(trustedhosts.CLIENT_KEY, trustedhosts.CLIENT_VALUE): "10.0.1.21"}
    assert read_trusted_hosts(fake_reader(client)) == ["10.0.1.21"]
    assert read_trusted_hosts(fake_reader({})) == []


def test_read_returns_none_when_unreadable():
    denied = {(trustedhosts.POLICY_KEY, trustedhosts.POLICY_VALUE): PermissionError()}
    assert read_trusted_hosts(fake_reader(denied)) is None


def _hosts_literal(script: str) -> str:
    """`$wanted = <문자열> | ConvertFrom-Json` 줄에서 <문자열>(따옴표 포함)을 꺼낸다."""
    marker, reader = "$wanted = ", " | ConvertFrom-Json"
    line = next(x for x in script.splitlines() if marker in x)
    start = line.index(marker) + len(marker)
    return line[start : line.index(reader, start)]


def test_build_add_script_carries_validated_hosts_only_inside_the_json_literal():
    hosts = ["10.0.1.21", "WEB-01.corp.local"]
    script = build_add_script(hosts)
    literal = _hosts_literal(script)
    assert literal[0] == literal[-1] == "'"
    inner = literal[1:-1]
    assert all(0x20 <= ord(c) <= 0x7E for c in inner), inner
    assert "'" not in inner
    assert json.loads(inner) == hosts
    # 리터럴을 자리표시로 되돌리면 템플릿과 같다: 서버 목록은 리터럴 밖 어디에도 들어가지 않는다.
    assert trustedhosts.template_text().count("__HQ_HOSTS__") == 1
    assert script.replace(literal, "__HQ_HOSTS__") == trustedhosts.template_text()
    assert "__HQ_HOSTS__" not in script
    with pytest.raises(ValidationError):
        build_add_script(["10.0.1.21; calc"])


def test_no_launched_script_decodes_base64():
    # AhnLab V3가 FromBase64String과 Invoke-Command가 함께 든 명령의 실행을 막는다(Ruling R4-1).
    assert "FromBase64String" not in build_add_script(["10.0.1.21"])


def test_template_never_opens_a_listener_or_changes_the_start_type():
    code = "\n".join(
        line for line in trustedhosts.template_text().splitlines() if not line.lstrip().startswith("#")
    ).lower()
    for banned in ("enable-psremoting", "quickconfig", "listener", "set-service", "startuptype"):
        assert banned not in code, banned


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (None, AddResult.CANCELLED),
        (0, AddResult.OK),
        (1, AddResult.FAILED),
        (2, AddResult.FAILED),
        (3, AddResult.FAILED),
        (4, AddResult.FAILED),
        (5, AddResult.FAILED),
        (259, AddResult.FAILED),  # STILL_ACTIVE: 기다리는 시간 안에 끝나지 않았다
    ],
)
def test_add_trusted_hosts_reports_the_outcome(code, expected):
    calls = []

    def run(file, parameters):
        calls.append((file, parameters))
        return code

    assert add_trusted_hosts(["10.0.1.21"], run=run) is expected
    assert len(calls) == 1
    file, parameters = calls[0]
    assert file.lower().endswith("powershell.exe")
    assert "-EncodedCommand" in parameters and "-NoExit" not in parameters


@pytest.mark.parametrize(
    ("code", "step"),
    [
        (2, "서버 목록 읽기"),
        (3, "WinRM 서비스 시작"),
        (4, "TrustedHosts 읽기"),
        (5, "TrustedHosts 쓰기"),
        (1, "알 수 없는 단계"),
    ],
)
def test_failure_logs_the_exit_code_and_step_as_a_warning(caplog, code, step):
    with caplog.at_level(logging.DEBUG, logger="hq.trustedhosts"):
        assert add_trusted_hosts(["10.0.1.21"], run=lambda *_: code) is AddResult.FAILED
    [record] = caplog.records
    assert (record.name, record.levelno) == ("hq.trustedhosts", logging.WARNING)
    assert f"종료 코드 {code}" in record.getMessage()
    assert step in record.getMessage()


@pytest.mark.parametrize("code", [None, 0])
def test_success_and_cancel_log_no_warning(caplog, code):
    with caplog.at_level(logging.DEBUG, logger="hq.trustedhosts"):
        add_trusted_hosts(["10.0.1.21"], run=lambda *_: code)
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_add_runs_the_script_built_for_the_unique_hosts():
    calls = []
    add_trusted_hosts(["10.0.1.21", "10.0.1.21", "web01"], run=lambda _file, parameters: calls.append(parameters) or 0)
    [parameters] = calls
    encoded = parameters.split()[-1]
    assert base64.b64decode(encoded).decode("utf-16-le") == build_add_script(["10.0.1.21", "web01"])


def test_add_nothing_does_not_ask_for_uac():
    assert add_trusted_hosts([], run=lambda *_: pytest.fail("UAC를 띄우면 안 된다")) is AddResult.OK


def test_shell_execute_info_matches_the_windows_layout():
    # SHELLEXECUTEINFOW는 64비트에서 112바이트, 32비트에서 60바이트다. cbSize가 틀리면 ShellExecuteExW가 거부한다.
    expected = 112 if ctypes.sizeof(ctypes.c_void_p) == 8 else 60
    assert ctypes.sizeof(trustedhosts._ShellExecuteInfo) == expected
