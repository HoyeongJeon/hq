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
        "ToUpper", "Contains", "StartsWith", "EndsWith", "AddDays", "AddHours", "AddMinutes", "AddSeconds",
        "IsMatch", "Match", "Matches",
    ]  # fmt: skip
    methods = next(x for x in spec.splitlines() if x.startswith("- 인스턴스 메서드:")).split("`")[1].split()
    assert rules["instance_methods"] == methods
    assert not {"Format", "Replace", "GetType", "IndexOf"} & set(rules["instance_methods"])
    assert rules["types"] == [
        "int", "long", "double", "decimal", "bool", "string", "char", "datetime", "timespan", "array", "hashtable",
        "ordered", "pscustomobject", "regex", "math", "xml",
    ]  # fmt: skip


def test_the_rules_file_closes_the_gaps_of_the_first_review():
    rules = load_rules()
    # Win32_Product의 부모 클래스. 조회하면 같은 Windows Installer 공급자가 자식 클래스까지 훑는다
    assert {"Win32_Product", "CIM_Product", "Win32_PingStatus"} <= set(rules["blocked_wmi_classes"])
    # 원격 매개변수가 파이프라인으로도 묶이는 명령(2026-11-11 이 PC의 명령 정보). 통합 시험이 같은 목록을 다시 뽑는다
    assert set(rules["pipeline_remote_commands"]) == {
        "Get-CimInstance", "Get-HotFix", "Get-NetIPConfiguration", "Get-Process", "Get-Service",
    }  # fmt: skip
    assert set(rules["pipeline_remote_commands"]) <= set(rules["commands"])


def test_the_rules_file_closes_the_gaps_of_the_expression_review():
    rules = load_rules()
    # 클래스 이름을 이어 붙여 만들면 이름 검사를 지나치므로 인자를 모두 글자 그대로 적게 한다
    assert rules["literal_argument_commands"] == ["Get-CimInstance"]
    # 해시테이블이 MachineName을 가진 개체로 바뀌어 묶인다
    assert "InputObject" in rules["remote_param_aliases"]["Get-Service"]
    assert "InputObject" in rules["remote_param_aliases"]["Get-Process"]
    # 새 세션에 없어 변수 목록에 잡히지 않는 환경 설정 변수. 실제 조회에 흔한 지역 이름(Event 등)은 넣지 않는다
    assert {
        "LogCommandHealthEvent", "LogCommandLifecycleEvent", "LogEngineHealthEvent", "LogEngineLifecycleEvent",
        "LogProviderHealthEvent", "LogProviderLifecycleEvent", "Transcript",
    } <= set(rules["extra_protected_variables"])  # fmt: skip
    assert not {"Event", "Sender", "EventArgs"} & set(rules["extra_protected_variables"])


def test_only_shaping_commands_may_take_a_script_block_argument():
    # 다른 명령에 준 스크립트 블록은 지연 바인딩되어 결과가 매개변수 값이 된다. 통합 시험이 이 명령들의 파이프라인
    # 매개변수가 입력 객체뿐임을 이 PC의 명령 정보로 확인한다
    rules = load_rules()
    assert rules["script_block_commands"] == [
        "Where-Object", "ForEach-Object", "Select-Object", "Sort-Object", "Group-Object", "Measure-Object",
        "Compare-Object", "Format-Table", "Format-List", "Format-Wide",
    ]  # fmt: skip
    assert set(rules["script_block_commands"]) <= set(rules["commands"])


def test_every_short_name_in_the_spec_maps_to_an_allowed_command():
    rules = load_rules()
    spec = (ROOT / "docs/specs/2026-11-04-hq-mcp-design.md").read_text(encoding="utf-8")
    line = next(x for x in spec.splitlines() if x.startswith("줄임말:"))
    assert sorted(rules["aliases"]) == sorted(line.removeprefix("줄임말:").split())
    assert set(rules["aliases"].values()) <= set(rules["commands"])
