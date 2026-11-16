"""hq.mcp.results: 제어 문자 지우기, 크기 세기, 결과 JSON을 크기 30,000 안에 서버별로 나눠 담기."""

import dataclasses
import json

import pytest

from hq.mcp.results import MEMO_CUT, build_result, build_server_list, clean_text, weighted_size
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


def test_seconds_are_rounded_to_one_decimal():
    text = build_result([dataclasses.replace(one("a" * 40_000), seconds=2.0123456789)])
    assert json.loads(text)["results"][0]["seconds"] == 2.0 and weighted_size(text) <= 30_000


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


def test_an_already_truncated_ascii_result_fills_the_budget_exactly():
    # 이미 truncated인 행은 false가 true로 바뀌며 한 글자 줄지 않으므로, ASCII 출력은 크기를 정확히 채운다
    assert weighted_size(build_result([dataclasses.replace(one("a" * 40_000), truncated=True)])) == 30_000


def test_small_servers_between_big_ones_are_not_cut_and_the_rest_is_shared():
    outcomes = [
        RunOutcome(f"s{i} (10.0.1.{i})", "OK", "x" * (50 if i % 3 == 0 else 5_000), "", 0, False, 1.0)
        for i in range(30)
    ]
    text = build_result(outcomes)
    rows = json.loads(text)["results"]
    small = [r for i, r in enumerate(rows) if i % 3 == 0]
    big = [len(r["output"]) for i, r in enumerate(rows) if i % 3]
    assert all(len(r["output"]) == 50 and not r["truncated"] for r in small)
    assert max(big) - min(big) <= 1
    assert 30_000 - len(big) <= weighted_size(text) <= 30_000  # 작은 서버가 남긴 몫은 큰 서버가 쓴다


def test_errors_keep_a_quarter_when_both_overflow():
    row = json.loads(build_result([one("o" * 40_000, "e" * 40_000)]))["results"][0]
    assert 0.2 < len(row["errors"]) / (len(row["errors"]) + len(row["output"])) < 0.3


def test_errors_use_what_output_leaves_and_an_errors_cut_is_reported():
    row = json.loads(build_result([one("o" * 10, "e" * 40_000)]))["results"][0]
    assert row["output"] == "o" * 10 and len(row["errors"]) > 29_000 and row["truncated"] is True


def test_an_outcome_already_truncated_stays_truncated():
    row = json.loads(build_result([dataclasses.replace(one("짧은 출력"), truncated=True)]))["results"][0]
    assert row["output"] == "짧은 출력" and row["truncated"] is True


def test_control_characters_are_removed_but_newlines_and_tabs_stay():
    assert clean_text("a\x1b[2Jb\r\nc\td\x00\x9b") == "a[2Jb\nc\td"


def test_lone_cr_vt_and_ff_are_removed():
    assert clean_text("a\rb\x0bc\x0cd") == "abcd"


def test_tag_characters_and_bidi_overrides_are_removed_but_joiners_stay():
    # 태그 문자는 보이지 않고, 방향 제어 문자는 글자 순서를 바꿔 보이게 한다. 둘 다 사람 몰래 Claude에게 글을 보낸다
    assert clean_text("a\U000e0041\U000e007fb\u202ec\u2066d\u2069e\u200d") == "abcde\u200d"


def test_a_note_appears_only_when_there_is_one():
    note = "강제로 끝냈습니다. 서버에서 계속 돌 수 있습니다."
    assert json.loads(build_result([one("", note=note)]))["results"][0]["note"].startswith("강제로")
    assert "note" not in json.loads(build_result([one("")]))["results"][0]


def test_no_outcomes_give_an_empty_list():
    assert json.loads(build_result([])) == {"results": []}


def test_a_budget_smaller_than_the_frame_empties_every_text():
    rows = json.loads(build_result([one("출력", "오류"), one("")], budget=10))["results"]
    assert [(r["output"], r["errors"], r["truncated"]) for r in rows] == [("", "", True), ("", "", False)]


# truncated=True면 틀의 truncated가 바뀌지 않아 남는 한 글자가 없으므로, 자르기가 한 글자만 넘쳐도 걸린다
@pytest.mark.parametrize("truncated", [False, True])
def test_odd_characters_are_cut_into_valid_json_at_every_size(truncated):
    # 짝 없는 서로게이트, 한 글자인 이모지, 서로게이트 두 글자(chr로 만든 짝)로 된 이모지, 이스케이프되는 글자,
    # 지울 제어 문자
    piece = "\ud800a😀" + chr(0xD83D) + chr(0xDE00) + '"\\\n\t가\x1b\r\n\udfff'
    outcome = dataclasses.replace(one(piece * 50, piece * 50), truncated=truncated)
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


def server_rows(count, memo_length):
    memo = '메모 "x"\\\n' * memo_length  # 따옴표, 역슬래시, 줄바꿈은 JSON에서 두 글자가 된다
    return [
        {
            "target": f"서버{n} (192.0.2.{n})",
            "name": f"서버{n}",
            "nickname": "",
            "host": f"192.0.2.{n}",
            "group": "운영",
            "memo": memo * (n % 3),
            "has_password": True,
        }
        for n in range(count)
    ]


@pytest.mark.parametrize("budget", [4_000, 9_000, 30_000])
def test_a_server_list_fits_the_budget_by_cutting_only_memos(budget):
    rows = server_rows(20, 300)
    text = build_server_list(rows, budget=budget)
    reply = json.loads(text)
    assert weighted_size(text) <= budget < weighted_size(build_server_list(rows, budget=10**9))
    for row, sent in zip(rows, reply["servers"], strict=True):
        assert {k: v for k, v in sent.items() if k != "memo"} == {k: v for k, v in row.items() if k != "memo"}
        cut = sent["memo"].endswith(MEMO_CUT)
        assert sent["memo"] == row["memo"] or (cut and row["memo"].startswith(sent["memo"][: -len(MEMO_CUT)]))
    cut = sum(sent["memo"] != row["memo"] for row, sent in zip(rows, reply["servers"], strict=True))
    assert cut > 0 and f"메모 {cut}개" in reply["note"]


def test_a_memo_is_cut_rather_than_dropped_when_the_reply_without_memos_fits():
    rows = [{**row, "memo": ""} for row in server_rows(40, 0)]
    budget = weighted_size(build_server_list(rows)) + 300  # memo 없는 응답에 300만 더 들어간다
    rows[7] = {**rows[7], "memo": "가" * 300}  # 1,200이라 통째로는 들지 않는다
    text = build_server_list(rows, budget=budget)
    sent = json.loads(text)["servers"][7]["memo"]
    assert weighted_size(text) <= budget
    assert sent.endswith(MEMO_CUT) and sent.startswith("가")


def test_a_server_list_that_fits_is_unchanged_and_has_no_note():
    rows = server_rows(3, 2)
    assert json.loads(build_server_list(rows)) == {"servers": rows}


def test_a_server_list_whose_frame_is_over_the_budget_drops_every_memo():
    rows = server_rows(20, 300)
    reply = json.loads(build_server_list(rows, budget=1_000))
    assert [row["memo"] for row in reply["servers"]] == [""] * 20
    assert f"메모 {sum(1 for row in rows if row['memo'])}개를 모두 뺐습니다" in reply["note"]
