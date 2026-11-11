"""조회 검사(스펙 7장). run_query가 스크립트를 확인 없이 실행해도 되는지 정한다.

허용 목록으로 판단한다. 글자 검사(textcheck.parser_hazard)에 걸리면 파서를 부르지 않고 거절하고, 아니면 이 PC의
powershell.exe에서 mcp-check.ps1이 스크립트를 파싱해 문법 트리를 살핀다. 스크립트는 자료로만 넘기고 실행하지 않는다.

닫힌 쪽으로 실패한다. 검사기를 띄우지 못하거나, 0이 아닌 종료 코드로 끝나거나, 시간 안에 끝나지 않거나, 출력이 정해진
모양이 아니면 CHECK_FAILED로 거절한다. 통과는 검사기가 정해진 모양으로 통과라고 쓴 경우뿐이다.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass

from hq.mcp.psrun import PsResult, run_ps_template
from hq.mcp.textcheck import parser_hazard
from hq.paths import resource

_REASON_KEYS = {"code", "message", "line"}


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reasons: tuple[str, ...] = ()  # "CODE: 설명 (줄 N)". 통과면 비어 있다


def load_rules() -> dict:
    """허용 목록 자료 파일(query_rules.json). 목록을 늘리려면 이 파일을 고치고 테스트를 거친다(스펙 7.2)."""
    return json.loads(resource("mcp/query_rules.json").read_text(encoding="utf-8"))


def check_query(
    script: str,
    *,
    rules: dict | None = None,
    run: Callable[..., PsResult] = run_ps_template,
    timeout: float = 10.0,
) -> Verdict:
    """스크립트를 검사한다. 검사기를 띄우지 못해도 예외 대신 CHECK_FAILED 거절을 돌려준다."""
    hazard = parser_hazard(script)
    if hazard:
        return Verdict(False, (f"PARSER_HAZARD: {hazard} 일반 실행(run_script)으로는 실행할 수 있습니다.",))
    payload = {"script": script, "rules": load_rules() if rules is None else rules}
    try:
        result = run(resource("ps/mcp-check.ps1"), payload, timeout=timeout)
    except OSError:
        return _failed("검사 프로세스(powershell.exe)를 띄우지 못했습니다.")
    if result.killed:
        return _failed(f"검사가 {timeout:g}초 안에 끝나지 않았습니다.")
    if result.code != 0:
        return _failed(f"검사 프로세스가 종료 코드 {result.code}로 끝났습니다.")
    verdict = _read(result.stdout)
    return _failed("검사 결과를 읽을 수 없습니다.") if verdict is None else verdict


def _failed(message: str) -> Verdict:
    return Verdict(False, (f"CHECK_FAILED: {message}",))


def _read(stdout: str) -> Verdict | None:
    """검사기가 쓴 한 줄 {"ok", "reasons": [{"code", "message", "line"}]}. 모양이 하나라도 다르면 None."""
    try:
        data = json.loads(stdout)
    except ValueError:
        return None
    if not isinstance(data, dict) or set(data) != {"ok", "reasons"}:
        return None
    ok, items = data["ok"], data["reasons"]
    if not isinstance(ok, bool) or not isinstance(items, list):
        return None
    reasons = []
    for item in items:
        if not isinstance(item, dict) or set(item) != _REASON_KEYS:
            return None
        code, message, line = item["code"], item["message"], item["line"]
        if not (isinstance(code, str) and isinstance(message, str) and type(line) is int):
            return None
        reasons.append(f"{code}: {message} (줄 {line})")
    if ok == bool(reasons):  # 통과인데 이유가 있거나, 거절인데 이유가 없다
        return None
    return Verdict(ok, tuple(reasons))
