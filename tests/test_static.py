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
