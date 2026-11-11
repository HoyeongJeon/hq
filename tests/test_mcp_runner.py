"""hq.mcp.runner를 가짜 run으로 확인한다: 종료 코드를 상태로 바꾸기, 강제 종료, 시간 한도, 잘림, 띄우지 못함."""

from pathlib import Path

import pytest

from hq.mcp.psrun import PsResult
from hq.mcp.runner import ServerJob, run_on_server

JOB = ServerJob("웹01 (10.0.1.21)", "10.0.1.21", "admin", Path("C:/s/a.dpapi"), "Get-Date", 30, Path("C:/c/1.cancel"))


def outcome(code, *, killed=False, trusted=("10.0.1.21",), out="o", err="e", total=2):
    return run_on_server(
        JOB, trusted=trusted, run=lambda *a, **k: PsResult(None if killed else code, out, err, total, killed)
    )


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (0, "OK"),
        (2, "SCRIPT_ERROR"),
        (10, "AUTH_FAILED"),
        (11, "UNREACHABLE"),
        (13, "QUOTA"),
        (14, "SECRET_UNREADABLE"),
        (20, "DISCONNECTED"),
        (21, "TIMEOUT"),
        (22, "CANCELLED"),
        (99, "UNKNOWN"),
    ],
)
def test_exit_codes_become_statuses(code, status):
    assert outcome(code).status == status


def test_1312_on_a_trusted_host_is_a_login_failure_and_otherwise_not_trusted():
    assert outcome(12).status == "AUTH_FAILED"
    assert outcome(12, trusted=()).status == "NOT_TRUSTED" and outcome(12, trusted=None).status == "NOT_TRUSTED"


def test_a_forced_kill_is_a_timeout_that_warns_the_script_may_still_run():
    result = outcome(None, killed=True)
    assert result.status == "TIMEOUT" and "서버에서 계속 돌 수 있습니다" in result.note


def test_the_process_gets_the_timeout_plus_ten_seconds_and_the_payload_has_six_keys():
    seen = {}

    def run(template, payload, *, timeout, **k):
        seen.update(name=template.name, keys=sorted(payload), timeout=timeout)
        return PsResult(0, "", "", 0, False)

    run_on_server(JOB, trusted=None, run=run)
    assert seen == {
        "name": "mcp-run.ps1",
        "timeout": 40,
        "keys": ["cancel_path", "host", "script", "secret_path", "timeout_seconds", "username"],
    }


def test_truncated_and_spawn_failure():
    assert outcome(0, out="x" * 10, err="", total=50).truncated and outcome(0, total=2).truncated is False

    def boom(*a, **k):
        raise OSError("no")

    assert run_on_server(JOB, trusted=None, run=boom).status == "SPAWN_FAILED"


def test_the_outcome_carries_both_streams_the_count_and_the_note_of_21():
    result = outcome(21, out="앞\n", err="뒤\n", total=50)
    assert (result.target, result.output, result.errors, result.output_chars, result.exit_code) == (
        "웹01 (10.0.1.21)",
        "앞\n",
        "뒤\n",
        50,
        21,
    )
    assert result.note == "시간 한도에서 멈췄습니다. 그 전까지는 서버에서 실행됐습니다."
    assert outcome(0).note == "" and outcome(None, killed=True).exit_code is None
