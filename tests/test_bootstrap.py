import base64
import json
import re
import subprocess

import pytest

from hq import bootstrap
from hq.bootstrap import (
    NOT_READY_CODE,
    READY_MARKER,
    BootstrapParams,
    ExitReason,
    build_command,
    build_script,
    prelude,
    ps_json_literal,
    reason_for_exit,
)
from hq.validation import ValidationError, validate_host, validate_username

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
        (30, ExitReason.NOT_READY),
        (1, ExitReason.UNKNOWN),
        (-1, ExitReason.UNKNOWN),
        (None, ExitReason.UNKNOWN),
    ],
)
def test_reason_for_exit(code, reason):
    assert reason_for_exit(code) is reason


def test_reason_values_are_their_names():
    assert ExitReason.DISCONNECTED.value == "DISCONNECTED"
    assert ExitReason.NOT_READY.value == "NOT_READY"


def test_not_ready_is_a_code_only_hq_reports():
    assert NOT_READY_CODE == 30
    assert reason_for_exit(NOT_READY_CODE) is ExitReason.NOT_READY
    # 스크립트는 이 코드로 끝나지 않는다. hq가 준비 신호를 기다리다 프로세스를 끝냈을 때만 쓴다.
    assert not re.search(r"Exit\(\s*30\s*\)|\bexit\s+30\b", bootstrap.template_text(), re.IGNORECASE)


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
    # Ctrl+C(PipelineStoppedException)는 catch를 건너뛰고 finally만 돈다. 그래서 종료는 finally 안에서만 하고,
    # 그 코드는 try가 무엇을 하기도 전에 정해져 있어야 한다.
    start = text.index("function global:prompt")
    safety_net = text[start : text.index("\n}\n", start)]
    assert safety_net.count("[Environment]::Exit") == 1
    assert re.search(r"\}\s*finally\s*\{\s*\[Environment\]::Exit\(\$code\)\s*\}", safety_net), safety_net
    assert safety_net.index("$code = 1") < safety_net.index("try {")


def test_template_has_one_placeholder_and_the_safety_net_first():
    text = bootstrap.template_text()
    assert text.count("__HQ_PARAMS__") == 1
    assert text.count(bootstrap.MAIN_MARKER) == 1
    assert text.index("function global:prompt") < text.index(bootstrap.MAIN_MARKER)


# ---- 준비 신호 (spec 7.3) ----


def _statements_after_the_safety_net() -> list[str]:
    text = prelude()
    start = text.index("function global:prompt")
    rest = text[text.index("\n}\n", start) + 3 :]
    return [line for line in rest.splitlines() if line.strip() and not line.lstrip().startswith("#")]


_SIGNAL = re.compile(
    r"if \(\$ExecutionContext\.SessionState\.LanguageMode -eq 'FullLanguage'\) "
    r"\{ \$Host\.UI\.RawUI\.WindowTitle = (?P<title>.+) \}"
)


def test_the_ready_signal_is_the_first_statement_after_the_safety_net():
    # 제한 언어 모드 등 FullLanguage가 아니면 안전장치의 [Environment]::Exit가 거부되므로 신호를 내지 않는다.
    # 제목 바꾸기도 함께 거부되는 데 기대지 않고 직접 확인한다. ConPTY는 제목을 제목 시퀀스(OSC)로 내보낸다.
    first = _statements_after_the_safety_net()[0]
    signal = _SIGNAL.fullmatch(first)
    assert signal, first
    assert "".join(re.findall(r"'([^']*)'", signal["title"])) == READY_MARKER, first


def test_the_ready_marker_never_appears_whole_in_the_script_text():
    # 줄이 실패하면 PowerShell 오류 메시지가 그 줄의 원문을 그대로 보여 준다. 원문에 표시가 통째로 있으면 hq가
    # 그 오류 메시지를 준비 신호로 착각해, 안전장치가 듣지 않는 탭에 입력을 보낼 수 있다. 그래서 표시는 실행할 때
    # 조각을 이어 만들고, 매개변수(자유 글자인 별명 포함) 안에서도 통째로 나오지 못하게 한다.
    assert READY_MARKER not in bootstrap.template_text()
    assert READY_MARKER not in build_script(PARAMS)
    marked = BootstrapParams(PARAMS.host, PARAMS.username, f"web {READY_MARKER}", PARAMS.secret_path)
    assert READY_MARKER not in build_script(marked)
    assert _payload(build_script(marked))["nickname"] == f"web {READY_MARKER}"


def test_json_literal_never_holds_the_ready_marker_whole():
    # 값 안의 ':'를 \u003a로 바꾼다. 준비 신호에는 ':'가 들어 있으므로 어떤 값에서도 통째로 나올 수 없다.
    assert ":" in READY_MARKER
    value = {"nickname": f"web {READY_MARKER}", "path": r"C:\x", "list": [READY_MARKER]}
    literal = ps_json_literal(value)
    assert READY_MARKER not in literal
    assert json.loads(literal[1:-1]) == value


def test_the_ready_marker_cannot_be_a_host_or_user_name():
    # 부트스트랩은 접속하기 전에 host를 화면에 찍는다. 그 글자가 준비 신호로 읽히면 안 된다.
    assert READY_MARKER.isascii()
    with pytest.raises(ValidationError):
        validate_host(READY_MARKER)
    with pytest.raises(ValidationError):
        validate_username(READY_MARKER)
