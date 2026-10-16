"""화면 파일의 구조와 금지 사항. DOM 동작은 체크포인트 B에서 사람이 확인한다."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from hq.bootstrap import ExitReason
from hq.sessions import _TERMINAL_REPLIES

STATIC = Path(__file__).resolve().parents[1] / "src" / "hq" / "static"


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


def csp(html):
    match = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', html)
    assert match, "CSP meta가 없다"
    directives = (part.strip().partition(" ") for part in match.group(1).split(";"))
    return {name: value for name, _, value in directives if name}


def test_index_has_a_strict_csp_and_loads_scripts_in_order():
    html = read("index.html")
    policy = csp(html)
    assert policy["default-src"] == "'none'"
    # pywebview 6.2.1은 JS API를 new Function으로 만들고 evaluate_js를 eval로 감싼다(ruling P4)
    assert policy["script-src"] == "'self' file: 'unsafe-eval'"
    assert policy["connect-src"] == "'none'"
    assert policy["base-uri"] == "'none'"
    assert policy["form-action"] == "'none'"
    assert not [value for value in policy.values() if "http" in value or "*" in value]
    order = [html.index(s) for s in ("vendor/xterm/xterm.js", "vendor/xterm/addon-fit.js", "js/logic.js", "js/app.js")]
    assert order == sorted(order)
    assert 'type="module"' not in html


@pytest.mark.parametrize("name", ["index.html", "css/app.css", "js/app.js", "js/logic.js"])
def test_no_external_resources(name):
    urls = re.findall(r"https?://[^\s'\")]+", read(name))
    assert [u for u in urls if not u.startswith("http://www.w3.org/")] == []


def test_screen_code_never_builds_html_from_strings():
    for name in ("js/app.js", "js/logic.js"):
        text = read(name)
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            assert banned not in text, f"{name}: {banned}"


def test_terminal_titles_never_reach_the_window():
    # 부트스트랩이 탭마다 콘솔 제목을 준비 신호로 바꾼다. 탭 제목과 창 제목은 hq가 정한 것만 쓴다
    text = read("js/app.js")
    assert "onTitleChange" not in text
    assert "document.title" not in text


def test_all_terminal_input_goes_through_the_tab_queue():
    # 키 입력과 확인을 받은 붙여넣기가 같은 줄을 타야 순서가 지켜진다
    text = read("js/app.js")
    assert text.count("send_input") == 1
    assert re.search(r"createInputQueue\(\(data\) => api\.send_input\(tab\.sid, data\)\)", text)


def function_body(text, signature):
    """app.js의 맨 바깥 함수(들여쓰기 2칸) 하나의 본문."""
    match = re.search(re.escape(signature) + r" \{\n(.*?)\n  \}\n", text, re.S)
    assert match, signature
    return match.group(1)


def test_reconnect_lets_terminal_replies_through_and_resends_a_missed_size():
    # 다시 연결하는 동안 새 ConPTY의 질의(DA1 등)에 답하지 못하면 탭 시작이 약 3초 늦어진다(Fix round 1, 1번)
    text = read("js/app.js")
    assert "tab.term.onData((data) => { if (L.shouldSendInput(tab, data)) tab.input.push(data); });" in text
    body = function_body(text, "async function startSession(tab, mode)")
    assert body.index("tab.term.options.disableStdin = false;") < body.index("await ")
    assert "tab.term.options.disableStdin = Boolean(tab.ended);" in body  # 실패하면 잠금을 되돌린다
    assert re.search(r"if \(tab\.term\.cols !== cols \|\| tab\.term\.rows !== rows\) api\.resize\(", body)


def test_trusting_hosts_from_an_ended_tab_reconnects_it():
    text = read("js/app.js")
    assert function_body(text, "async function trustNow()").rstrip().endswith("return result.ok;")
    assert "if (await trustNow()) startSession(tab, 'reconnect');" in function_body(
        text, "async function endedAction(tab, id)")


def test_trust_dialog_is_closed_before_an_ended_tab_reconnects():
    # 다시 연결이 성공하면 터미널이 포커스를 가져간다. 안내 창이 열린 채라면 창을 닫으려는 Enter가 원격 셸로 간다
    body = function_body(read("js/app.js"), "async function trustNow()")
    assert body.count("messageDialog(") == body.count("await messageDialog(") == 1
    assert body.index("await messageDialog(") < body.index("return result.ok;")


def test_terminals_take_focus_only_through_the_modal_aware_helper():
    # 대화 상자가 열려 있을 때 터미널이 포커스를 가져가면 대화 상자에 치는 글자(비밀번호 포함)가 원격 셸로 간다
    text = read("js/app.js")
    helper = function_body(text, "function focusTerminal(tab)")
    assert "$('#modal').hidden" in helper
    assert text.count("term.focus()") == helper.count("term.focus()") == 1
    # 다음 프레임에 주는 포커스도 그 순간에 확인한다
    activate = function_body(text, "function activate(sid)")
    assert re.search(r"requestAnimationFrame\(\(\) => \{[^}]*focusTerminal\(tab\);", activate)


def test_an_open_dialog_makes_everything_behind_it_inert():
    # 탭 이동(Tab, Shift+Tab)으로 뒤의 터미널이나 서버 목록에 닿으면 대화 상자에 치는 글자가 원격 셸로 간다
    assert 'class="app"' in read("index.html")
    body = function_body(read("js/app.js"), "function openModal({ title, body, buttons, wide })")
    assert "closeCurrentModal(undefined, true);" in body  # 다른 창으로 바꿀 때는 뒤 화면을 잠근 채로 둔다
    assert body.index("$('.app').inert = true;") < body.index("focusTarget.focus()")
    # 마지막 창이 닫힐 때만 뒤 화면을 풀고 포커스를 지금 탭의 터미널로 돌려준다
    assert re.search(r"if \(!replaced\) \{\s*\$\('\.app'\)\.inert = false;[^}]*focusTerminal\(", body)
    assert body.count(".inert = false") == 1
    assert "event.key === 'Tab'" in body  # 대화 상자의 처음과 끝에서 포커스가 문서 밖으로 나가지 않게 한다


def test_a_rejected_session_call_takes_the_normal_failure_path():
    text = read("js/app.js")
    body = function_body(text, "async function startSession(tab, mode)")
    assert "await L.callHq(" in body
    for call in ("api.open_session(", "api.reconnect("):
        assert text.count(call) == body.count(call) == 1, call


def test_ctrl_c_keeps_the_selection_when_copying_failed():
    body = function_body(read("js/app.js"), "function terminalKey(tab, event)")
    assert body.count("clearSelection()") == 1
    assert "if (copied) tab.term.clearSelection();" in body


def test_paste_into_an_ended_tab_is_ignored_before_any_dialog():
    body = function_body(read("js/app.js"), "function onPaste(tab, event)")
    assert "L.pasteAction(text, tab.ended)" in body
    assert body.index("if (action === 'ignore') return;") < body.index("confirmDialog(")


def test_index_has_the_places_app_js_needs():
    html = read("index.html")
    for element_id in ("rail-servers", "rail-settings", "servers-view", "settings-view", "settings-body", "refresh",
                       "server-list", "add-server", "excel", "search", "banner", "load-error", "tabbar", "panes",
                       "empty", "info", "menu", "modal"):
        assert f'id="{element_id}"' in html, element_id


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_scripts_have_no_syntax_errors():
    for name in ("js/app.js", "js/logic.js"):
        subprocess.run(["node", "--check", str(STATIC / name)], check=True)


# ---------- Python과 logic.js가 같은 규칙을 쓰는지 ----------
def alternatives(body):
    return body.split("|")  # 각 갈래의 문자 집합 안에는 '|'가 없다


def test_terminal_reply_rule_matches_python():
    js = re.search(r"const TERMINAL_REPLIES = /\^\(\?:(.*)\)\+\$/;", read("js/logic.js"))
    py = re.fullmatch(r"\(\?:(.*)\)\+", _TERMINAL_REPLIES.pattern)
    assert js and py
    assert alternatives(js.group(1)) == alternatives(py.group(1))


def test_every_exit_reason_has_an_ended_view():
    block = re.search(r"const ENDED = \{(.*?)\n  \};", read("js/logic.js"), re.S)
    assert block
    assert set(re.findall(r"^    ([A-Z_]+):", block.group(1), re.M)) == {reason.value for reason in ExitReason}
