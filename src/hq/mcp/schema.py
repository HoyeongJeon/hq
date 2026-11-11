"""세 기능의 입력 모양, 힌트, _meta. 숫자는 스펙 4.5장을 따른다."""

LIST_SERVERS = "list_servers"
RUN_QUERY = "run_query"
RUN_SCRIPT = "run_script"
REQUIRES_USER_INTERACTION = "anthropic/requiresUserInteraction"


def tool_shapes() -> dict[str, dict]:
    """기능 이름 → {"input_schema", "annotations", "meta"}. 부를 때마다 새로 만든다."""
    return {
        LIST_SERVERS: {
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": {"readOnlyHint": True},
            "meta": None,
        },
        RUN_QUERY: {
            "input_schema": _run_input(with_reason=False),
            "annotations": {"readOnlyHint": True},
            "meta": None,
        },
        RUN_SCRIPT: {
            "input_schema": _run_input(with_reason=True),
            "annotations": {"readOnlyHint": False, "destructiveHint": True},
            # 값은 JSON true여야 한다. 이 표시가 있으면 Claude Code가 호출마다 사람에게 묻는다
            "meta": {REQUIRES_USER_INTERACTION: True},
        },
    }


def _run_input(*, with_reason: bool) -> dict:
    properties = {
        "targets": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True},
        "script": {"type": "string", "minLength": 1, "maxLength": 20000},
    }
    required = ["targets", "script"]
    if with_reason:
        properties["reason"] = {"type": "string", "minLength": 1, "maxLength": 200}
        required.append("reason")
    properties["timeout_seconds"] = {"type": "integer", "minimum": 5, "maximum": 1500, "default": 120}
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}
