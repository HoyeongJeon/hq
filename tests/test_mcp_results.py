"""hq.mcp.results: 제어 문자 지우기, 크기 세기, 결과 JSON을 크기 30,000 안에 서버별로 나눠 담기."""

import dataclasses
import json

import pytest

from hq.mcp.results import build_result, clean_text, weighted_size
from hq.mcp.runner import RunOutcome


def one(output="", errors="", **k):
    return RunOutcome("웹01 (10.0.1.21)", "OK", output, errors, len(output) + len(errors), False, 1.5, **k)


def test_small_results_are_untouched_and_hangul_is_not_escaped():
    text = build_result([one("메모리 16GB\n", note="")])
    assert json.loads(text) == {
        "results": [
            {
                "target": "웹01 (10.0.1.21)",
                "status": "OK",
                "output": "메모리 16GB\n",
                "errors": "",
                "truncated": False,
                "output_chars": 9,
                "seconds": 1.5,
            }
        ]
    }
    assert "메모리" in text and "\\u" not in text


# id를 따로 준다. 값으로 만든 id는 Windows의 환경 변수 한도(PYTEST_CURRENT_TEST, 32,767자)를 넘는다
@pytest.mark.parametrize(
    "output",
    ["\\\n" * 20_000, "가" * 40_000, '"' * 40_000, "a" * 40_000],
    ids=["backslash-newline", "hangul", "quote", "ascii"],
)
def test_the_finished_json_never_exceeds_the_budget(output):
    text = build_result([one(output)])
    row = json.loads(text)["results"][0]
    assert weighted_size(text) <= 30_000 and len(text) < 50_000
    assert row["truncated"] is True and row["output_chars"] == len(output) and output.startswith(row["output"])


def test_hangul_only_output_keeps_at_most_7500_characters():
    assert len(json.loads(build_result([one("가" * 40_000)]))["results"][0]["output"]) <= 7_500


def test_thirty_servers_share_the_budget_and_small_ones_are_not_cut():
    outcomes = [
        RunOutcome(f"s{i} (10.0.1.{i})", "OK", "x" * (50 if i < 10 else 5_000), "", 0, False, 1.0) for i in range(30)
    ]
    text = build_result(outcomes)
    rows = json.loads(text)["results"]
    assert weighted_size(text) <= 30_000 and [r["target"] for r in rows] == [o.target for o in outcomes]
    assert all(len(r["output"]) == 50 and not r["truncated"] for r in rows[:10])
    big = [len(r["output"]) for r in rows[10:]]
    assert min(big) > 0 and max(big) - min(big) <= 1 and all(r["truncated"] for r in rows[10:])


def test_errors_keep_a_quarter_when_both_overflow():
    row = json.loads(build_result([one("o" * 40_000, "e" * 40_000)]))["results"][0]
    assert 0.2 < len(row["errors"]) / (len(row["errors"]) + len(row["output"])) < 0.3


def test_control_characters_are_removed_but_newlines_and_tabs_stay():
    assert clean_text("a\x1b[2Jb\r\nc\td\x00\x9b") == "a[2Jb\nc\td"


def test_a_note_appears_only_when_there_is_one():
    note = "강제로 끝냈습니다. 서버에서 계속 돌 수 있습니다."
    assert json.loads(build_result([one("", note=note)]))["results"][0]["note"].startswith("강제로")
    assert "note" not in json.loads(build_result([one("")]))["results"][0]


def test_no_outcomes_give_an_empty_list():
    assert json.loads(build_result([])) == {"results": []}


def test_a_budget_smaller_than_the_frame_empties_every_text():
    rows = json.loads(build_result([one("출력", "오류"), one("")], budget=10))["results"]
    assert [(r["output"], r["errors"], r["truncated"]) for r in rows] == [("", "", True), ("", "", False)]


def test_odd_characters_are_cut_into_valid_json_at_every_size():
    # 짝 없는 서로게이트, 한 글자인 이모지, 서로게이트 두 글자(chr로 만든 짝)로 된 이모지, 이스케이프되는 글자,
    # 지울 제어 문자
    piece = "\ud800a😀" + chr(0xD83D) + chr(0xDE00) + '"\\\n\t가\x1b\r\n\udfff'
    outcome = one(piece * 50, piece * 50)
    cleaned = clean_text(piece * 50)
    frame = weighted_size(build_result([dataclasses.replace(outcome, output="", errors="", truncated=True)]))
    last_kept = set()
    for budget in range(frame - 30, frame + 300):  # 틀보다 작은 크기부터 글자 경계를 하나씩 모두 지난다
        text = build_result([outcome], budget=budget)
        row = json.loads(text)["results"][0]
        assert row["truncated"] is True and weighted_size(text) <= max(budget, frame)
        assert cleaned.startswith(row["output"]) and cleaned.startswith(row["errors"])
        last_kept.add(row["output"][-1:])
    assert chr(0xD83D) in last_kept  # 두 글자 사이에서 자른 경우도 지났다
