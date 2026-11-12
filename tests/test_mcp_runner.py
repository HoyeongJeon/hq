"""hq.mcp.runner를 가짜 run으로 확인한다: 종료 코드를 상태로 바꾸기, 강제 종료, 시간 한도, 잘림, 띄우지 못함."""

import dataclasses
import os
from pathlib import Path

import pytest

from hq.mcp.psrun import PsResult
from hq.mcp.runner import ServerJob, run_on_server

JOB = ServerJob("웹01 (10.0.1.21)", "10.0.1.21", "admin", Path("C:/s/a.dpapi"), "Get-Date", 30, Path("C:/c/1.cancel"))


def outcome(code, *, killed=False, trusted=("10.0.1.21",), out="o", err="e", total=2, job=JOB):
    return run_on_server(
        job, trusted=trusted, run=lambda *a, **k: PsResult(None if killed else code, out, err, total, killed)
    )


def job_in(tmp_path):
    """취소 파일이 임시 폴더 아래인 작업. 강제로 끝낸 경우는 취소 파일이 있는지 보므로 실제 경로를 보지 않게 한다."""
    return dataclasses.replace(JOB, cancel_path=tmp_path / "1.cancel")


def unreadable(monkeypatch, path):
    """Windows가 path의 속성 읽기를 거부하듯 os.stat이 PermissionError를 던지게 한다. Path.exists는 다시 던진다.

    Python 3.12의 os.path.exists(nt._path_exists)는 바꾼 os.stat을 부르지 않는다. 그래서 이 도우미를 쓰는 시험은
    확인이 더는 os.stat을 거치지 않음을 보일 뿐, 확인 안에서 난 오류를 삼키는 것까지 보이지는 않는다.
    """
    real_stat = os.stat

    def stat(target, *args, **kwargs):
        if isinstance(target, (str, os.PathLike)) and os.fspath(target) == os.fspath(path):
            raise PermissionError(13, "액세스가 거부되었습니다", os.fspath(path))
        return real_stat(target, *args, **kwargs)

    monkeypatch.setattr(os, "stat", stat)


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


def test_a_forced_kill_is_a_timeout_that_warns_the_script_may_still_run(tmp_path):
    result = outcome(None, killed=True, job=job_in(tmp_path))
    assert result.status == "TIMEOUT" and "서버에서 계속 돌 수 있습니다" in result.note


def test_the_process_gets_the_timeout_plus_ten_seconds_and_the_payload_has_six_keys():
    seen = {}

    def run(template, payload, *, timeout, **k):
        seen.update(name=template.name, keys=sorted(payload), timeout=timeout)
        seen.update(stop_path=k["stop_path"], stop_grace=k["stop_grace"])
        return PsResult(0, "", "", 0, False)

    run_on_server(JOB, trusted=None, run=run)
    assert seen == {
        "name": "mcp-run.ps1",
        "timeout": 40,
        "keys": ["cancel_path", "host", "script", "secret_path", "timeout_seconds", "username"],
        "stop_path": JOB.cancel_path,
        "stop_grace": 10,
    }


def test_truncated_and_spawn_failure():
    assert outcome(0, out="x" * 10, err="", total=50).truncated and outcome(0, total=2).truncated is False

    def boom(*a, **k):
        raise OSError("no")

    assert run_on_server(JOB, trusted=None, run=boom).status == "SPAWN_FAILED"


def test_the_outcome_carries_both_streams_the_count_and_the_note_of_21(tmp_path):
    result = outcome(21, out="앞\n", err="뒤\n", total=50)
    assert (result.target, result.output, result.errors, result.output_chars, result.exit_code) == (
        "웹01 (10.0.1.21)",
        "앞\n",
        "뒤\n",
        50,
        21,
    )
    assert result.note == "시간 한도에서 멈췄습니다. 그 전까지는 서버에서 실행됐습니다."
    assert outcome(0).note == "" and outcome(None, killed=True, job=job_in(tmp_path)).exit_code is None


def test_a_kill_after_a_cancel_is_cancelled_and_warns_the_script_may_still_run(tmp_path):
    cancel = tmp_path / "1.cancel"
    cancel.touch()
    job = dataclasses.replace(JOB, cancel_path=cancel)
    result = run_on_server(job, trusted=None, run=lambda *a, **k: PsResult(None, "", "", 0, True))
    assert (result.status, result.note) == ("CANCELLED", "강제로 끝냈습니다. 서버에서 계속 돌 수 있습니다.")


def test_a_kill_whose_cancel_file_cannot_be_checked_is_a_timeout(tmp_path, monkeypatch):
    # 취소 파일을 보다 난 오류는 "취소 파일 없음"이다. run_on_server는 예외를 올리지 않는다
    job = job_in(tmp_path)
    unreadable(monkeypatch, job.cancel_path)
    result = run_on_server(job, trusted=None, run=lambda *a, **k: PsResult(None, "", "", 0, True))
    assert (result.status, result.note) == ("TIMEOUT", "강제로 끝냈습니다. 서버에서 계속 돌 수 있습니다.")


def test_a_disconnect_warns_that_the_script_may_still_run():
    assert outcome(20).note == "연결이 끊겼습니다. 서버에서 계속 돌 수 있습니다."
