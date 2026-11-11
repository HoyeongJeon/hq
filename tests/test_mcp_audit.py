"""hq.mcp.audit: 실행 기록 파일에 시작, 끝, 거절을 한 줄씩 이어 쓰기."""

import hashlib
import json
import logging
import threading
from datetime import datetime

import pytest

from hq.mcp.audit import AuditLog
from hq.mcp.runner import RunOutcome


def lines(tmp_path):
    path = tmp_path / "mcp-audit-2026-11.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_start_records_the_whole_script_and_its_hash(tmp_path):
    log = AuditLog(tmp_path, now=lambda: datetime(2026, 11, 10, 9, 0, 0))
    log.start("c1", "run_script", [("웹01 (10.0.1.21)", "10.0.1.21")], "Get-Date # 한글", "점검")
    assert lines(tmp_path) == [
        {
            "event": "start",
            "time": "2026-11-10T09:00:00",
            "call": "c1",
            "tool": "run_script",
            "targets": [{"target": "웹01 (10.0.1.21)", "host": "10.0.1.21"}],
            "script": "Get-Date # 한글",
            "sha256": hashlib.sha256("Get-Date # 한글".encode()).hexdigest(),
            "reason": "점검",
        }
    ]


def test_end_never_holds_server_output(tmp_path):
    log = AuditLog(tmp_path, now=lambda: datetime(2026, 11, 10))
    log.end("c1", [RunOutcome("t", "OK", "비밀 출력", "오류 글자", 9, False, 2.0, exit_code=0)], 2.5)
    text = (tmp_path / "mcp-audit-2026-11.jsonl").read_text(encoding="utf-8")
    assert "비밀 출력" not in text and "오류 글자" not in text
    assert lines(tmp_path)[0]["results"] == [
        {"target": "t", "status": "OK", "exit_code": 0, "seconds": 2.0, "output_chars": 9}
    ]
    assert lines(tmp_path) == [
        {
            "event": "end",
            "time": "2026-11-10T00:00:00",
            "call": "c1",
            "seconds": 2.5,
            "results": [{"target": "t", "status": "OK", "exit_code": 0, "seconds": 2.0, "output_chars": 9}],
        }
    ]


def test_a_start_without_a_reason_writes_null(tmp_path):
    AuditLog(tmp_path, now=lambda: datetime(2026, 11, 10)).start("c2", "run_query", [], "Get-Date", None)
    assert lines(tmp_path) == [
        {
            "event": "start",
            "time": "2026-11-10T00:00:00",
            "call": "c2",
            "tool": "run_query",
            "targets": [],
            "script": "Get-Date",
            "sha256": hashlib.sha256(b"Get-Date").hexdigest(),
            "reason": None,
        }
    ]


def test_start_raises_when_the_record_cannot_be_written(tmp_path):
    (tmp_path / "blocked").write_text("x")  # 폴더 자리에 파일
    with pytest.raises(OSError):
        AuditLog(tmp_path / "blocked").start("c", "run_query", [], "x", None)


def test_start_raises_and_end_warns_when_the_month_file_cannot_be_opened(tmp_path, caplog):
    (tmp_path / "mcp-audit-2026-11.jsonl").mkdir()  # 파일 자리에 폴더
    log = AuditLog(tmp_path, now=lambda: datetime(2026, 11, 10))
    with pytest.raises(OSError):
        log.start("c", "run_query", [], "x", None)
    with caplog.at_level(logging.WARNING, logger="hq.mcp.audit"):
        log.end("c", [], 1.0)
    assert [r.levelno for r in caplog.records if r.name == "hq.mcp.audit"] == [logging.WARNING]


def test_eight_threads_never_mix_their_lines(tmp_path):
    log = AuditLog(tmp_path, now=lambda: datetime(2026, 11, 10))
    ready = threading.Barrier(8)

    def write(n):
        ready.wait()
        for i in range(50):  # 한 줄이 파일 버퍼(8,192바이트)보다 길다
            log.start(f"c{n}-{i}", "run_script", [("웹01 (10.0.1.21)", "10.0.1.21")], "가" * 3_000, None)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    records = lines(tmp_path)
    assert len(records) == 400 and all(r["script"] == "가" * 3_000 for r in records)
    assert {r["call"] for r in records} == {f"c{n}-{i}" for n in range(8) for i in range(50)}


def test_two_logs_on_one_folder_never_lose_a_line(tmp_path):
    # 두 hq.mcp 프로세스(Claude Code 창 둘)가 같은 달 파일에 쓰는 경우. 잠금은 객체마다 따로다
    logs = [AuditLog(tmp_path, now=lambda: datetime(2026, 11, 10)) for _ in range(2)]
    ready = threading.Barrier(8)

    def write(n):
        ready.wait()
        for i in range(50):  # 한 줄이 파일 버퍼(8,192바이트)보다 길다
            logs[n % 2].start(f"c{n}-{i}", "run_script", [("웹01 (10.0.1.21)", "10.0.1.21")], "가" * 3_000, None)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    records = lines(tmp_path)  # 깨진 줄이 있으면 여기서 JSON으로 읽지 못한다
    assert all(r["script"] == "가" * 3_000 for r in records)
    assert sorted(r["call"] for r in records) == sorted(f"c{n}-{i}" for n in range(8) for i in range(50))


def test_a_rejection_is_recorded_with_the_script_and_the_reason(tmp_path):
    log = AuditLog(tmp_path, now=lambda: datetime(2026, 11, 10, 9, 30, 15))
    log.rejected("run_query", "Stop-Service W3SVC", "허용하지 않는 명령입니다: Stop-Service")
    assert lines(tmp_path) == [
        {
            "event": "rejected",
            "time": "2026-11-10T09:30:15",
            "tool": "run_query",
            "script": "Stop-Service W3SVC",
            "why": "허용하지 않는 명령입니다: Stop-Service",
        }
    ]


def test_the_file_name_follows_the_month(tmp_path):
    AuditLog(tmp_path, now=lambda: datetime(2026, 12, 1)).rejected("run_query", "x", "y")
    assert [p.name for p in tmp_path.iterdir()] == ["mcp-audit-2026-12.jsonl"]


def test_records_are_appended_to_a_folder_made_when_missing(tmp_path):
    log = AuditLog(tmp_path / "logs", now=lambda: datetime(2026, 11, 10))
    log.start("c1", "run_query", [], "Get-Date", None)
    log.end("c1", [], 0.5)
    assert [r["event"] for r in lines(tmp_path / "logs")] == ["start", "end"]


def test_end_only_warns_when_the_record_cannot_be_written(tmp_path, caplog):
    (tmp_path / "blocked").write_text("x")
    with caplog.at_level(logging.WARNING, logger="hq.mcp.audit"):
        AuditLog(tmp_path / "blocked").end("c", [RunOutcome("t", "OK")], 1.0)
    assert [r.levelno for r in caplog.records if r.name == "hq.mcp.audit"] == [logging.WARNING]


def test_a_rejection_only_warns_when_the_record_cannot_be_written(tmp_path, caplog):
    (tmp_path / "blocked").write_text("x")
    with caplog.at_level(logging.WARNING, logger="hq.mcp.audit"):
        AuditLog(tmp_path / "blocked").rejected("run_query", "x", "y")
    assert [r.levelno for r in caplog.records if r.name == "hq.mcp.audit"] == [logging.WARNING]


def test_characters_some_readers_take_as_line_breaks_stay_inside_one_line(tmp_path):
    # str.splitlines()와 일부 편집기는 U+0085, U+2028, U+2029도 줄바꿈으로 본다
    script = "a\x85b\N{LINE SEPARATOR}c\N{PARAGRAPH SEPARATOR}d\ne\rf\x00g"
    AuditLog(tmp_path, now=lambda: datetime(2026, 11, 10)).rejected("run_query", script, "이유")
    text = (tmp_path / "mcp-audit-2026-11.jsonl").read_text(encoding="utf-8")
    assert len(text.splitlines()) == 1 and json.loads(text)["script"] == script


def test_a_lone_surrogate_from_claude_is_recorded_as_it_came(tmp_path):
    # JSON의 "\ud800" 이스케이프는 짝 없는 서로게이트가 되어 스크립트에 들어온다. UTF-8로 그냥 쓰면 예외가 난다
    log = AuditLog(tmp_path, now=lambda: datetime(2026, 11, 10))
    log.start("c1", "run_query", [], "Get-Date \ud800", None)
    log.rejected("run_query", "Get-Date \udfff", "이유")
    first, second = lines(tmp_path)
    assert first["script"] == "Get-Date \ud800" and second["script"] == "Get-Date \udfff"
    assert first["sha256"] == hashlib.sha256("Get-Date \ud800".encode("utf-8", "surrogatepass")).hexdigest()
