import base64
import json
import subprocess

import pytest

from hq import bootstrap
from hq.bootstrap import (
    BootstrapParams,
    ExitReason,
    build_command,
    build_script,
    prelude,
    ps_json_literal,
    reason_for_exit,
)
from hq.validation import ValidationError

PARAMS = BootstrapParams(
    host="10.0.1.21",
    username="Administrator",
    nickname="웹01'; Remove-Item C:\\ -Recurse; '",
    secret_path=r"C:\Users\전호영\AppData\Local\hq\secrets\0123.dpapi",
)
# 한글, ', ", $, 백틱, ;, 줄바꿈, PowerShell이 작은따옴표로 치는 U+2018~U+201B, 이모지(서로게이트 쌍)
NASTY = "웹01'; Remove-Item C:\\ -Recurse; \"$x`;\n\u2018\u2019\u201a\u201b 🙂"


def _payload(script: str) -> dict:
    marker = "$p = '"
    start = script.index(marker) + len(marker)
    return json.loads(script[start : script.index("'", start)])


def test_json_literal_is_printable_ascii_with_no_quote_inside():
    literal = ps_json_literal({"nickname": NASTY})
    assert literal[0] == literal[-1] == "'"
    inner = literal[1:-1]
    assert all(0x20 <= ord(c) <= 0x7E for c in inner), inner
    assert "'" not in inner
    assert json.loads(inner) == {"nickname": NASTY}


def test_json_literal_escapes_the_delete_character_too():
    inner = ps_json_literal("a\x7fb")[1:-1]
    assert all(0x20 <= ord(c) <= 0x7E for c in inner), inner
    assert json.loads(inner) == "a\x7fb"


def test_no_launched_script_decodes_base64():
    # AhnLab V3가 FromBase64String과 Invoke-Command가 함께 든 명령의 실행을 막는다.
    assert "FromBase64String" not in bootstrap.template_text()
    assert "FromBase64String" not in build_script(PARAMS)


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        (0, ExitReason.USER_EXIT),
        (10, ExitReason.AUTH_FAILED),
        (11, ExitReason.UNREACHABLE),
        (12, ExitReason.NOT_TRUSTED),
        (13, ExitReason.QUOTA),
        (14, ExitReason.SECRET_UNREADABLE),
        (20, ExitReason.DISCONNECTED),
        (1, ExitReason.UNKNOWN),
        (-1, ExitReason.UNKNOWN),
        (None, ExitReason.UNKNOWN),
    ],
)
def test_reason_for_exit(code, reason):
    assert reason_for_exit(code) is reason


def test_reason_values_are_their_names():
    assert ExitReason.DISCONNECTED.value == "DISCONNECTED"


def test_values_travel_only_inside_the_json_literal():
    expected = {
        "host": "10.0.1.21",
        "username": "Administrator",
        "nickname": PARAMS.nickname,
        "secret_path": PARAMS.secret_path,
    }
    script = build_script(PARAMS)
    literal = ps_json_literal(expected)
    assert script.count(literal) == 1
    assert script.replace(literal, "__HQ_PARAMS__") == bootstrap.template_text()
    assert "Remove-Item C:" not in bootstrap.template_text()
    assert "전호영" not in script
    assert _payload(script) == expected


def test_rejects_bad_host_and_username():
    with pytest.raises(ValidationError):
        build_script(BootstrapParams("10.0.1.21; calc", "Administrator", "x", "C:\\s"))
    with pytest.raises(ValidationError):
        build_script(BootstrapParams("10.0.1.21", "admin'", "x", "C:\\s"))


def test_build_command_runs_windows_powershell_without_profile():
    argv = build_command(PARAMS)
    assert argv[0].lower().endswith("\\windowspowershell\\v1.0\\powershell.exe")
    assert argv[1:5] == ["-NoLogo", "-NoProfile", "-NoExit", "-EncodedCommand"]
    assert base64.b64decode(argv[5]).decode("utf-16-le") == build_script(PARAMS)


def test_encoded_argv_can_skip_noexit():
    assert "-NoExit" not in bootstrap.encoded_argv("Write-Output 1", no_exit=False)


def test_command_line_stays_well_under_the_windows_limit():
    long = BootstrapParams("a" * 63 + ".corp.local", "x" * 64, "가" * 50, "C:\\" + "d\\" * 100 + "s.dpapi")
    assert len(subprocess.list2cmdline(build_command(long))) < 30000


def test_prelude_holds_the_safety_net_and_nothing_that_connects():
    text = prelude()
    assert "function global:prompt" in text
    assert "[Environment]::Exit" in text
    assert "function Get-HqExitCode" in text
    assert "New-PSSession" not in text
    assert "__HQ_PARAMS__" not in text


def test_template_has_one_placeholder_and_the_safety_net_first():
    text = bootstrap.template_text()
    assert text.count("__HQ_PARAMS__") == 1
    assert text.count(bootstrap.MAIN_MARKER) == 1
    assert text.index("function global:prompt") < text.index(bootstrap.MAIN_MARKER)
