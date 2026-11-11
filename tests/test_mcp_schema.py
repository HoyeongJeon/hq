import json

from hq.mcp.schema import tool_shapes


def test_only_run_script_asks_the_user_and_the_value_is_json_true():
    shapes = tool_shapes()
    assert shapes["run_script"]["meta"] == {"anthropic/requiresUserInteraction": True}
    assert json.loads(json.dumps(shapes["run_script"]["meta"]))["anthropic/requiresUserInteraction"] is True
    assert shapes["list_servers"]["meta"] is None and shapes["run_query"]["meta"] is None


def test_hints():
    shapes = tool_shapes()
    assert shapes["list_servers"]["annotations"] == {"readOnlyHint": True}
    assert shapes["run_query"]["annotations"] == {"readOnlyHint": True}
    assert shapes["run_script"]["annotations"] == {"readOnlyHint": False, "destructiveHint": True}


def test_input_limits_match_the_spec():
    query = tool_shapes()["run_query"]["input_schema"]
    assert query["required"] == ["targets", "script"] and query["additionalProperties"] is False
    assert query["properties"]["targets"] == {
        "type": "array",
        "items": {"type": "string"},
        "minItems": 1,
        "uniqueItems": True,
    }
    assert query["properties"]["script"] == {"type": "string", "minLength": 1, "maxLength": 20000}
    assert query["properties"]["timeout_seconds"] == {"type": "integer", "minimum": 5, "maximum": 1500, "default": 120}
    script = tool_shapes()["run_script"]["input_schema"]
    assert script["required"] == ["targets", "script", "reason"]
    assert script["properties"]["reason"] == {"type": "string", "minLength": 1, "maxLength": 200}
    assert tool_shapes()["list_servers"]["input_schema"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
