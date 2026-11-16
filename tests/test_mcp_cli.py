import io
import json

import pytest

from hq.inventory import Inventory
from hq.mcp.access import Access, load_access, load_servers, save_access
from hq.mcp.cli import main


def run(tmp_path, *argv):
    out = io.StringIO()
    code = main(argv, data=tmp_path, out=out)
    return code, out.getvalue()


def inventory(tmp_path, servers):
    """(이름, host, 그룹)들을 임시 폴더의 inventory.json에 쓴다."""
    inv = Inventory(tmp_path / "inventory.json")
    for name, host, group in servers:
        inv.add({"name": name, "host": host, "username": "admin", "group": group})
    inv.save()


def enabled_hosts(tmp_path):
    return [host for _, host in load_access(tmp_path / "mcp-access.json").enabled]


def line_with(text, word):
    return next(line for line in text.splitlines() if word in line)


def test_on_enables_by_host_and_list_shows_the_three_states(tmp_path):
    inventory(tmp_path, [("웹01", "10.0.1.21", "운영"), ("디비", "10.0.1.22", "운영"), ("금지", "10.0.1.99", "운영")])
    assert run(tmp_path, "never", "10.0.1.99")[0] == 0
    assert run(tmp_path, "on", "10.0.1.21")[0] == 0
    code, text = run(tmp_path, "list")
    lines = text.splitlines()
    assert code == 0
    assert any("웹01" in line and "켜짐" in line for line in lines)
    assert any("디비" in line and "꺼짐" in line for line in lines)
    assert any("10.0.1.99" in line and "금지" in line for line in lines)


def test_on_refuses_a_forbidden_host_and_changes_nothing(tmp_path):
    inventory(tmp_path, [("웹01", "10.0.1.21", "운영"), ("디비", "10.0.1.99", "운영")])
    assert run(tmp_path, "never", "10.0.1.99")[0] == 0
    code, text = run(tmp_path, "on", "10.0.1.21", "10.0.1.99")
    assert code == 2 and "금지" in line_with(text, "10.0.1.99")
    assert enabled_hosts(tmp_path) == []


def test_on_group_skips_forbidden_servers_and_says_so(tmp_path):
    inventory(
        tmp_path,
        [
            ("웹01", "10.0.1.21", "운영"),
            ("디비", "10.0.1.22", "운영"),
            ("창고", "10.0.1.99", "운영"),
            ("시험", "192.0.2.7", "시험"),
        ],
    )
    assert run(tmp_path, "never", "10.0.1.99")[0] == 0
    code, text = run(tmp_path, "on", "--group", "운영")
    assert code == 0 and "건너뜁니다" in line_with(text, "10.0.1.99")
    assert enabled_hosts(tmp_path) == ["10.0.1.21", "10.0.1.22"]
    assert run(tmp_path, "on", "--group", "없는 그룹")[0] == 2


def test_on_group_finds_ungrouped_servers_by_the_name_hq_shows(tmp_path):
    # HQ 화면은 그룹이 빈 서버를 미분류 아래에 보여 준다
    inventory(tmp_path, [("웹01", "10.0.1.21", ""), ("디비", "10.0.1.22", "운영")])
    assert run(tmp_path, "on", "--group", "미분류")[0] == 0
    assert enabled_hosts(tmp_path) == ["10.0.1.21"]
    assert "미분류" in line_with(run(tmp_path, "list")[1], "웹01")


def test_never_removes_the_server_from_enabled_and_accepts_unknown_ips(tmp_path):
    inventory(tmp_path, [("웹01", "10.0.1.21", "운영"), ("디비", "10.0.1.22", "운영")])
    assert run(tmp_path, "on", "10.0.1.21", "10.0.1.22")[0] == 0
    code, text = run(tmp_path, "never", "10.0.1.21", "192.0.2.7")
    assert code == 0 and "192.0.2.7" in text
    access = load_access(tmp_path / "mcp-access.json")
    assert enabled_hosts(tmp_path) == ["10.0.1.22"] and access.never == ("10.0.1.21", "192.0.2.7")


def test_never_compares_addresses_by_value(tmp_path):
    inventory(tmp_path, [("웹01", "10.0.1.21", "운영"), ("디비", "10.0.1.99", "운영")])
    assert run(tmp_path, "on", "::ffff:10.0.1.21", "10.0.1.99")[0] == 0
    assert enabled_hosts(tmp_path) == ["10.0.1.21", "10.0.1.99"]
    assert run(tmp_path, "never", "::ffff:10.0.1.99")[0] == 0
    assert enabled_hosts(tmp_path) == ["10.0.1.21"]
    assert "금지" in line_with(run(tmp_path, "list")[1], "10.0.1.99")
    assert run(tmp_path, "on", "10.0.1.99")[0] == 2


def test_never_refuses_a_name(tmp_path):
    code, text = run(tmp_path, "never", "10.0.1.99", "web01")
    assert code == 2 and "IP" in line_with(text, "web01")
    assert load_access(tmp_path / "mcp-access.json").never == ()  # 앞의 IP도 넣지 않았다


def test_on_refuses_an_unknown_host(tmp_path):
    inventory(tmp_path, [("웹01", "10.0.1.21", "운영")])
    code, text = run(tmp_path, "on", "10.0.1.21", "10.0.1.30")
    assert code == 2 and "목록에 없습니다" in line_with(text, "10.0.1.30")
    assert enabled_hosts(tmp_path) == []


def test_on_refuses_a_name_with_control_characters_and_list_does_not_print_them(tmp_path):
    inventory(tmp_path, [("웹01", "10.0.1.21", "운영")])
    path = tmp_path / "inventory.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["servers"][0]["nickname"] = "\x1b[2J웹"  # 예전 hq가 저장했을 수 있는 별명. 찍으면 화면이 지워진다
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    code, text = run(tmp_path, "on", "10.0.1.21")
    assert code == 2 and "제어 문자" in text and "\x1b" not in text
    code, text = run(tmp_path, "on", "--group", "운영")
    assert code == 0 and "건너뜁니다" in text and "\x1b" not in text
    assert enabled_hosts(tmp_path) == []
    text = run(tmp_path, "list")[1]
    assert "\x1b" not in text and "\\x1b[2J웹" in text


RLO = "\N{RIGHT-TO-LEFT OVERRIDE}"  # 뒤의 글자를 거꾸로 보이게 한다
# (MCP가 쓰지 않는 글자, 명령줄이 대신 찍는 글자). 소스에는 이름이나 chr로만 적는다
HIDDEN = [
    (RLO, "\\u202e"),
    ("\N{ZERO WIDTH SPACE}", "\\u200b"),
    ("\N{TAG LATIN CAPITAL LETTER A}", "\\U000e0041"),
    ("\N{LINE SEPARATOR}", "\\u2028"),
    (chr(0xE000), "\\ue000"),  # 사용자 정의 글자(Co)
    (chr(0x378), "\\u0378"),  # 정해지지 않은 글자(Cn)
    ("\x1b", "\\x1b"),
]


def edit_first_server(tmp_path, **fields):
    """HQ 창이 받는 값이지만 MCP는 쓰지 않는 값을 inventory.json에 직접 넣는다."""
    path = tmp_path / "inventory.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["servers"][0].update(fields)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize(
    "char, escaped", HIDDEN, ids=["rlo", "zwsp", "tag", "line-sep", "private", "unassigned", "esc"]
)
def test_a_name_the_mcp_rule_refuses_is_not_enabled_and_list_says_why(tmp_path, char, escaped):
    inventory(tmp_path, [("웹01", "10.0.1.21", "운영")])
    edit_first_server(tmp_path, nickname=f"웹{char}01")
    texts = []
    code, text = run(tmp_path, "on", "10.0.1.21")
    texts.append(text)
    assert code == 2 and "보이지 않는 글자" in text and f"웹{escaped}01 (10.0.1.21)" in text
    code, text = run(tmp_path, "on", "--group", "운영")
    texts.append(text)
    assert code == 0 and "건너뜁니다" in text and enabled_hosts(tmp_path) == []
    (server,), _ = load_servers(tmp_path / "inventory.json")
    save_access(tmp_path / "mcp-access.json", Access(enabled=((server.id, "10.0.1.21"),)))  # 파일을 고쳐 켰다
    code, text = run(tmp_path, "list")
    texts.append(text)
    line = line_with(text, "10.0.1.21")
    assert code == 0 and line.startswith("쓸 수 없음") and f"웹{escaped}01 (10.0.1.21)" in line
    assert "보이지 않는 글자" in line and "HQ에서 별명이나 이름을 고치세요" in line
    assert not any(char in text for text in texts)


def test_the_command_line_uses_the_same_rule_for_a_name_behind_a_clean_nickname(tmp_path):
    inventory(tmp_path, [("웹01", "10.0.1.21", "운영")])
    edit_first_server(tmp_path, name="웹\N{HANGUL FILLER}01", nickname="웹")
    code, text = run(tmp_path, "on", "10.0.1.21")
    assert code == 2 and "보이지 않는 글자" in text and enabled_hosts(tmp_path) == []
    code, text = run(tmp_path, "list")
    assert code == 0 and line_with(text, "10.0.1.21").startswith("쓸 수 없음")


def test_list_escapes_the_spaces_and_fillers_the_name_rule_refuses(tmp_path):
    inventory(tmp_path, [("웹01", "10.0.1.21", "운영")])
    edit_first_server(tmp_path, group="운\N{NO-BREAK SPACE}\N{HANGUL FILLER}영")
    text = run(tmp_path, "list")[1]
    assert "운\\xa0\\u3164영" in text and "\N{NO-BREAK SPACE}" not in text and "\N{HANGUL FILLER}" not in text


def test_every_printed_line_escapes_what_the_terminal_would_act_on(tmp_path):
    inventory(tmp_path, [("웹01", "10.0.1.21", "운영")])
    edit_first_server(tmp_path, group=f"운{RLO}영")
    outputs = [
        run(tmp_path, "list"),
        run(tmp_path, "off", f"web{RLO}01"),  # 사람이 친 인자도 그대로 찍지 않는다
        run(tmp_path, "on", "--group", f"없는{RLO}그룹"),
        run(tmp_path, "never", f"10.0.1.{RLO}9"),
    ]
    assert [code for code, _ in outputs] == [0, 0, 2, 2]
    texts = [text for _, text in outputs]
    assert "운\\u202e영" in texts[0] and "web\\u202e01" in texts[1]
    assert "없는\\u202e그룹" in texts[2] and "10.0.1.\\u202e9" in texts[3]
    assert not any(RLO in text for text in texts)


def test_the_usage_is_printed_line_by_line(tmp_path):
    code, text = run(tmp_path, "help")
    lines = text.splitlines()
    assert code == 2 and lines[0] == "사용법:" and len(lines) == 6 and "\\x0a" not in text


def test_on_keeps_one_entry_per_server(tmp_path):
    # HQ에서 host를 고친 서버를 다시 켜면 예전 host 항목이 남지 않는다. 남으면 host를 되돌리면 다시 켜진다
    inventory(tmp_path, [("웹01", "10.0.1.21", "운영")])
    (server,), _ = load_servers(tmp_path / "inventory.json")
    save_access(tmp_path / "mcp-access.json", Access(enabled=((server.id, "10.0.1.30"),)))
    assert run(tmp_path, "on", "10.0.1.21")[0] == 0
    assert run(tmp_path, "on", "10.0.1.21")[0] == 0
    assert load_access(tmp_path / "mcp-access.json").enabled == ((server.id, "10.0.1.21"),)


def test_on_and_off_find_a_name_host_in_any_case(tmp_path):
    inventory(tmp_path, [("웹01", "10.0.1.21", "운영"), ("웹02", "WEB02", "운영")])
    assert run(tmp_path, "on", "10.0.1.21", "web02")[0] == 0
    assert enabled_hosts(tmp_path) == ["10.0.1.21", "web02"]
    assert "켜짐" in line_with(run(tmp_path, "list")[1], "WEB02")
    code, text = run(tmp_path, "off", "Web02", "10.0.1.30")
    assert code == 0 and "켜져 있지 않습니다" in line_with(text, "10.0.1.30")
    assert enabled_hosts(tmp_path) == ["10.0.1.21"]
    assert "꺼짐" in line_with(run(tmp_path, "list")[1], "WEB02")


@pytest.mark.parametrize(
    "argv",
    [("on", "10.0.1.21"), ("on", "--group", "운영"), ("off", "10.0.1.21"), ("never", "10.0.1.99")],
    ids=["on", "on-group", "off", "never"],
)
def test_a_broken_settings_file_is_not_overwritten(tmp_path, argv):
    inventory(tmp_path, [("웹01", "10.0.1.21", "운영")])
    path = tmp_path / "mcp-access.json"
    path.write_text("{broken", encoding="utf-8")
    code, text = run(tmp_path, *argv)
    assert code == 2 and "JSON" in text
    assert path.read_text(encoding="utf-8") == "{broken"


def test_list_says_why_a_broken_settings_file_turns_everything_off(tmp_path):
    inventory(tmp_path, [("웹01", "10.0.1.21", "운영")])
    assert run(tmp_path, "on", "10.0.1.21")[0] == 0
    (tmp_path / "mcp-access.json").write_text("{broken", encoding="utf-8")
    code, text = run(tmp_path, "list")
    assert code == 0 and "JSON" in text and "꺼짐" in line_with(text, "웹01")


@pytest.mark.parametrize("argv", [("list",), ("on", "10.0.1.21"), ("on", "--group", "운영")])
def test_commands_that_need_the_server_list_say_it_is_missing(tmp_path, argv):
    code, text = run(tmp_path, *argv)
    assert code == 2 and "서버 목록 파일이 없습니다" in text
    assert not (tmp_path / "mcp-access.json").exists()


@pytest.mark.parametrize(
    "argv",
    [("on", "--all"), ("off", "--all"), ("allow", "10.0.1.99"), ("unban", "10.0.1.99")],
)
def test_there_is_no_all_and_no_command_to_lift_a_ban(tmp_path, argv):
    inventory(tmp_path, [("웹01", "10.0.1.21", "운영"), ("디비", "10.0.1.99", "운영")])
    assert run(tmp_path, "never", "10.0.1.99")[0] == 0
    code, text = run(tmp_path, *argv)
    assert code == 2 and "사용법" in text
    access = load_access(tmp_path / "mcp-access.json")
    assert access.enabled == () and access.never == ("10.0.1.99",)


@pytest.mark.parametrize("argv", [(), ("list", "10.0.1.21"), ("on",), ("on", "--group"), ("never",)])
def test_a_wrong_command_line_prints_the_usage(tmp_path, argv):
    inventory(tmp_path, [("웹01", "10.0.1.21", "운영")])
    code, text = run(tmp_path, *argv)
    assert code == 2 and "사용법" in text
    assert not (tmp_path / "mcp-access.json").exists()
