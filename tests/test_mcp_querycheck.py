"""hq.mcp.querycheck를 가짜 run으로 확인한다. PowerShell을 띄우지 않는다."""

import re
from pathlib import Path

import pytest

from hq.mcp.psrun import PsResult
from hq.mcp.querycheck import check_query, load_rules

ROOT = Path(__file__).resolve().parents[1]


def test_a_parser_hazard_is_refused_without_starting_the_checker():
    calls = []
    verdict = check_query("using module \\\\z\\share\\m", run=lambda *a, **k: calls.append(a))
    assert not verdict.ok and verdict.reasons[0].startswith("PARSER_HAZARD") and calls == []


def test_the_parser_hazard_refusal_points_to_run_script():
    # 문자열이나 주석 속의 같은 글자도 걸리므로 일반 실행으로 돌릴 길을 알려 준다(스펙 4.3, 7.1)
    verdict = check_query("Get-Date # using module x", run=lambda *a, **k: pytest.fail("검사기를 띄웠다"))
    assert len(verdict.reasons) == 1 and "run_script" in verdict.reasons[0]


@pytest.mark.parametrize(
    "result",
    [PsResult(1, "", "boom", 4, False), PsResult(None, "", "", 0, True), PsResult(0, "not json", "", 8, False)],
)
def test_any_checker_failure_is_a_refusal(result):
    verdict = check_query("Get-Date", run=lambda *a, **k: result)
    assert not verdict.ok and verdict.reasons[0].startswith("CHECK_FAILED")


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "[]",
        '{"ok": true}',
        '{"ok": "true", "reasons": []}',
        '{"ok": true, "reasons": {}}',
        '{"ok": true, "reasons": [], "extra": 1}',
        '{"ok": true, "reasons": [{"code": "COMMAND", "message": "m", "line": 1}]}',  # 통과인데 이유가 있다
        '{"ok": false, "reasons": []}',  # 거절인데 이유가 없다
        '{"ok": false, "reasons": [{"code": "COMMAND", "message": "m"}]}',
        '{"ok": false, "reasons": [{"code": "COMMAND", "message": "m", "line": "1"}]}',
        '{"ok": false, "reasons": [{"code": "COMMAND", "message": "m", "line": true}]}',
        '{"ok": false, "reasons": ["COMMAND"]}',
        '{"ok": true, "reasons": []}\n{"ok": true, "reasons": []}',
    ],
)
def test_output_of_any_other_shape_is_a_refusal(stdout):
    verdict = check_query("Get-Date", run=lambda *a, **k: PsResult(0, stdout, "", len(stdout), False))
    assert not verdict.ok and verdict.reasons[0].startswith("CHECK_FAILED")


def test_a_checker_that_cannot_start_is_a_refusal():
    def run(*a, **k):
        raise OSError("powershell.exe 없음")

    verdict = check_query("Get-Date", run=run)
    assert not verdict.ok and verdict.reasons[0].startswith("CHECK_FAILED")


def test_each_reason_names_its_code_message_and_line():
    stdout = (
        '{"ok": false, "reasons": [{"code": "COMMAND", "message": "허용 목록에 없는 명령", "line": 2},'
        ' {"code": "PATH", "message": "UNC", "line": 3}]}\n'
    )
    verdict = check_query("Get-Date", run=lambda *a, **k: PsResult(0, stdout, "", len(stdout), False))
    assert verdict.ok is False and verdict.reasons == ("COMMAND: 허용 목록에 없는 명령 (줄 2)", "PATH: UNC (줄 3)")


def test_the_checker_gets_ten_seconds_and_the_rules():
    seen = {}

    def run(template, payload, *, timeout, **k):
        seen.update(template=template.name, keys=sorted(payload), timeout=timeout)
        return PsResult(0, '{"ok": true, "reasons": []}', "", 0, False)

    assert check_query("Get-Date", run=run).ok and seen == {
        "template": "mcp-check.ps1",
        "keys": ["rules", "script"],
        "timeout": 10.0,
    }


def test_given_rules_are_passed_as_they_are():
    seen = {}

    def run(template, payload, *, timeout, **k):
        seen.update(payload)
        return PsResult(0, '{"ok": true, "reasons": []}', "", 0, False)

    check_query("Get-Date", rules={"commands": ["Get-Date"]}, run=run)
    assert seen == {"script": "Get-Date", "rules": {"commands": ["Get-Date"]}}


def test_the_rules_file_lists_what_the_spec_lists():
    rules = load_rules()
    spec = (ROOT / "docs/specs/2026-11-04-hq-mcp-design.md").read_text(encoding="utf-8")
    block = spec.split("```text\n조회:", 1)[1].split("```", 1)[0]
    listed = re.findall(
        r"\b(?:Get|Select|Test|Where|ForEach|Sort|Group|Measure|Compare|Format|Out|ConvertTo|Write)-[A-Za-z]+", block
    )
    assert sorted(rules["commands"]) == sorted(set(listed))
    assert "Get-WmiObject" not in rules["commands"] and "Get-Command" not in rules["commands"]
    assert rules["instance_methods"] == [
        "ToString", "ToXml", "Trim", "TrimStart", "TrimEnd", "PadLeft", "PadRight", "Split", "Substring", "ToLower",
        "ToUpper", "Contains", "StartsWith", "EndsWith", "IndexOf", "AddDays", "AddHours", "AddMinutes", "AddSeconds",
        "IsMatch", "Match", "Matches",
    ]  # fmt: skip
    assert not {"Format", "Replace", "GetType"} & set(rules["instance_methods"])
    assert rules["types"] == [
        "int", "long", "double", "decimal", "bool", "string", "char", "datetime", "timespan", "array", "hashtable",
        "ordered", "pscustomobject", "regex", "math", "xml",
    ]  # fmt: skip


def test_every_short_name_in_the_spec_maps_to_an_allowed_command():
    rules = load_rules()
    spec = (ROOT / "docs/specs/2026-11-04-hq-mcp-design.md").read_text(encoding="utf-8")
    line = next(x for x in spec.splitlines() if x.startswith("줄임말:"))
    assert sorted(rules["aliases"]) == sorted(line.removeprefix("줄임말:").split())
    assert set(rules["aliases"].values()) <= set(rules["commands"])
