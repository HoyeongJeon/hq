import json
import logging
import socket
import time

import pytest

from hq.inventory import Inventory, InventoryError, Server
from hq.mcp.access import (
    Access,
    AccessError,
    canonical_ip,
    load_access,
    load_server_list,
    load_servers,
    name_problems,
    resolve_host,
    save_access,
    target_of,
    usable,
)

ID_A, ID_B = "a" * 32, "b" * 32
UNREADABLE = "서버 목록 파일(inventory.json)을 읽을 수 없습니다. HQ 창을 열어 확인하세요."


def write(path, data, bom=False):
    path.write_text(json.dumps(data), encoding="utf-8-sig" if bom else "utf-8")


def test_a_missing_file_means_everything_is_off_without_a_problem(tmp_path):
    assert load_access(tmp_path / "mcp-access.json") == Access()


def test_a_file_saved_by_notepad_with_a_bom_loads(tmp_path):
    write(tmp_path / "f", {"version": 1, "enabled": [{"id": ID_A, "host": "WEB01"}], "never": ["10.0.1.99"]}, bom=True)
    assert load_access(tmp_path / "f") == Access(enabled=((ID_A, "web01"),), never=("10.0.1.99",))


@pytest.mark.parametrize(
    "text, word",
    [
        ("{broken", "JSON"),
        ('{"version": 2, "enabled": [], "never": []}', "version"),
        ('{"version": 1, "enabled": [], "never": ["db01.example"]}', "db01.example"),
        ('{"version": 1, "enabled": [{"id": "x"}], "never": []}', "enabled"),
    ],
)
def test_a_broken_file_turns_everything_off_and_says_why(tmp_path, text, word):
    (tmp_path / "f").write_text(text, encoding="utf-8")
    access = load_access(tmp_path / "f")
    assert access.enabled == () and access.never == () and word in access.problem
    assert (tmp_path / "f").read_text(encoding="utf-8") == text
    with pytest.raises(AccessError):
        save_access(tmp_path / "f", access)


@pytest.mark.parametrize(
    "text, word",
    [
        # never가 빠지거나 이름을 잘못 적은 파일을 빈 목록으로 읽으면 금지 주소가 조용히 풀린다
        pytest.param('{"version": 1, "enabled": []}', "never", id="no-never"),
        pytest.param('{"version": 1, "enabled": [], "never": [], "nevr": ["10.0.1.99"]}', "nevr", id="unknown-key"),
        # 손으로 never 줄을 하나 더 넣으면 json은 뒤의 값만 남긴다
        pytest.param('{"version": 1, "enabled": [], "never": ["10.0.1.99"], "never": []}', "never", id="twice"),
        pytest.param('{"version": true, "enabled": [], "never": []}', "version", id="version-true"),
        pytest.param('{"version": 1, "enabled": {}, "never": []}', "enabled", id="enabled-object"),
        pytest.param('{"version": 1, "enabled": ["10.0.1.21"], "never": []}', "enabled", id="item-not-object"),
        pytest.param('{"version": 1, "enabled": [{"id": "x", "host": 5}], "never": []}', "enabled", id="host-number"),
        pytest.param('{"version": 1, "enabled": [], "never": [99]}', "99", id="never-number"),
        pytest.param("[1]", "객체", id="array"),
        pytest.param("[" * 100000, "JSON", id="deep"),
    ],
)
def test_a_file_of_the_wrong_shape_is_broken_too(tmp_path, text, word):
    (tmp_path / "f").write_text(text, encoding="utf-8")
    access = load_access(tmp_path / "f")
    assert access.enabled == () and access.never == () and word in access.problem


def test_an_unreadable_file_turns_everything_off_instead_of_raising(tmp_path):
    (tmp_path / "f").write_bytes(b"\xff\xfe{")  # UTF-8이 아니다
    assert load_access(tmp_path / "f").problem
    assert load_access(tmp_path).problem  # 폴더


def test_canonical_ip_compares_by_value():
    assert canonical_ip("::ffff:10.0.1.21") == "10.0.1.21" and canonical_ip(" 10.0.1.21 ") == "10.0.1.21"
    with pytest.raises(ValueError):
        canonical_ip("web01")


def test_canonical_ip_drops_the_ipv6_zone(tmp_path):
    assert canonical_ip("fe80::1%12") == canonical_ip("fe80::1") == "fe80::1"
    # never에 범위 표시를 붙여 적어도, 풀린 주소에 범위 표시가 붙어 와도 같은 주소로 비교한다
    write(tmp_path / "f", {"version": 1, "enabled": [], "never": ["fe80::1%12"]})
    assert load_access(tmp_path / "f").never == ("fe80::1",)

    def zoned(host, port):
        return [(23, 1, 6, "", ("fe80::1%12", 0, 0, 12))]

    assert resolve_host("web01", getaddrinfo=zoned) == ["fe80::1"]


def test_a_never_address_written_another_way_in_the_file_still_blocks_the_server(tmp_path):
    write(tmp_path / "f", {"version": 1, "enabled": [{"id": ID_A, "host": "10.0.1.21"}], "never": ["::ffff:10.0.1.21"]})
    access = load_access(tmp_path / "f")
    server = Server(ID_A, "웹", "10.0.1.21", "admin")
    assert usable([server], Access(enabled=access.enabled)) == [server]  # 켜져 있지만
    assert usable([server], access) == []  # 금지 주소다


def test_usable_needs_the_same_id_and_host_pair():
    a = Server(ID_A, "웹", "10.0.1.21", "admin", nickname="웹01")
    assert usable([a], Access(enabled=((ID_A, "10.0.1.21"),))) == [a]
    assert usable([a], Access(enabled=((ID_A, "10.0.1.22"),))) == []  # HQ에서 host를 고친 서버
    assert usable([a], Access(enabled=((ID_B, "10.0.1.21"),))) == []
    assert usable([a], Access(enabled=((ID_A, "10.0.1.21"),), never=("10.0.1.21",))) == []
    assert target_of(a) == "웹01 (10.0.1.21)"


def test_usable_ignores_the_case_of_the_host_and_leaves_names_to_the_lookup():
    named = Server(ID_A, "웹", "WEB01.example", "admin")
    # 이름 host는 never와 실행 전에 이름을 풀어 비교한다(resolve_host)
    assert usable([named], Access(enabled=((ID_A, "web01.example"),), never=("10.0.1.21",))) == [named]


def test_a_nickname_with_a_control_character_cannot_be_used():
    old = Server(ID_A, "웹", "10.0.1.21", "admin", nickname="웹\x1b[2J")
    assert usable([old], Access(enabled=((ID_A, "10.0.1.21"),))) == []


HIDDEN = {
    "rlo": "\u202e",  # 글자 순서를 뒤집어 보이게 한다
    "tag": "\U000e0041",
    "zero-width-space": "\u200b",
    "line-separator": "\u2028",
    "paragraph-separator": "\u2029",
    "bom": "\ufeff",
    "private-use": "\ue000",
    "unassigned": "\u0378",
    "lone-surrogate": "\ud800",
    "c1": "\x85",
}


@pytest.mark.parametrize("hidden", HIDDEN.values(), ids=HIDDEN.keys())
def test_a_target_name_with_an_invisible_or_direction_character_is_not_usable(hidden):
    by_nickname = Server(ID_A, "웹", "10.0.1.21", "admin", nickname=f"웹{hidden}01")
    by_name = Server(ID_B, f"디비{hidden}02", "10.0.1.22", "admin")  # 별명이 없어 이름이 target이 된다
    access = Access(enabled=((ID_A, "10.0.1.21"), (ID_B, "10.0.1.22")))
    assert usable([by_nickname, by_name], access) == []
    problems = name_problems([by_nickname, by_name])
    assert len(problems) == 2 and all(hidden not in problem for problem in problems)
    assert "1번째" in problems[0] and "10.0.1.21" in problems[0] and "2번째" in problems[1]


@pytest.mark.parametrize("name", ["웹서버 01", "Web-01_a (주)", "디비2", "app server (test)"])
def test_ordinary_names_stay_usable(name):
    servers = [Server(ID_A, name, "10.0.1.21", "admin"), Server(ID_B, "웹", "10.0.1.22", "admin", nickname=name)]
    access = Access(enabled=((ID_A, "10.0.1.21"), (ID_B, "10.0.1.22")))
    assert usable(servers, access) == servers and name_problems(servers) == []


def test_only_the_text_that_forms_the_target_is_judged():
    # 별명이 있으면 이름은 target에 들지 않는다. list_servers는 그 이름을 clean_text로 거른 뒤 보낸다
    server = Server(ID_A, "웹\u202e01", "10.0.1.21", "admin", nickname="웹")
    assert usable([server], Access(enabled=((ID_A, "10.0.1.21"),))) == [server] and name_problems([server]) == []


def test_resolve_returns_every_address_and_none_when_it_takes_too_long():
    def fake(host, port):
        return [
            (2, 1, 6, "", ("10.0.1.21", 0)),
            (23, 1, 6, "", ("::ffff:10.0.1.21", 0, 0, 0)),
            (2, 1, 6, "", ("10.0.1.22", 0)),
        ]

    assert resolve_host("web01", getaddrinfo=fake) == ["10.0.1.21", "10.0.1.22"]
    assert resolve_host("10.0.1.21", getaddrinfo=None) == ["10.0.1.21"]  # IP는 풀지 않는다

    def slow(host, port):
        time.sleep(5)

    started = time.monotonic()
    assert resolve_host("web01", getaddrinfo=slow, timeout=0.1) is None and time.monotonic() - started < 1


def test_resolve_returns_none_when_the_lookup_fails_or_gives_an_address_it_cannot_compare():
    def fail(host, port):
        raise socket.gaierror(11001, "getaddrinfo failed")

    def odd(host, port):
        return [(2, 1, 6, "", ("10.0.1.21", 0)), (99, 1, 6, "", ("web01", 0))]

    assert resolve_host("web01", getaddrinfo=fail) is None
    assert resolve_host("web01", getaddrinfo=odd) is None  # 비교하지 못한 주소가 금지 주소일 수 있다


def test_load_servers_reports_a_missing_or_broken_inventory(tmp_path):
    path = tmp_path / "inventory.json"
    servers, problem = load_servers(path)
    assert servers == [] and "서버 목록" in problem
    path.write_text("{broken", encoding="utf-8")
    servers, problem = load_servers(path)
    assert servers == [] and problem
    path.unlink()
    inventory = Inventory(path)
    added = inventory.add({"name": "웹", "host": "10.0.1.21", "username": "admin"})
    inventory.save()
    assert load_servers(path) == ([added], None)


def test_load_servers_reads_again_while_hq_replaces_the_file(tmp_path, monkeypatch):
    path = tmp_path / "inventory.json"
    inventory = Inventory(path)
    added = inventory.add({"name": "웹", "host": "10.0.1.21", "username": "admin"})
    inventory.save()
    real, calls, waits = Inventory.load, [], []

    def locked_twice(self):
        calls.append(1)
        if len(calls) <= 2:
            raise InventoryError("잠김") from PermissionError(5, "Access is denied")
        real(self)

    monkeypatch.setattr(Inventory, "load", locked_twice)
    monkeypatch.setattr("hq.mcp.access.time.sleep", waits.append)
    assert load_servers(path) == ([added], None)
    assert len(calls) == 3 and waits == [0.05, 0.05]


@pytest.mark.parametrize(
    "cause, reads, problem",
    [(PermissionError(5, "Access is denied"), 5, "잠김"), (ValueError("버전 2"), 1, UNREADABLE)],
    ids=["locked", "broken"],
)
def test_load_servers_reads_again_only_while_the_file_is_locked(tmp_path, monkeypatch, cause, reads, problem):
    (tmp_path / "inventory.json").write_text("{}", encoding="utf-8")
    calls, waits = [], []

    def failing(self):
        calls.append(1)
        raise InventoryError("잠김") from cause

    monkeypatch.setattr(Inventory, "load", failing)
    monkeypatch.setattr("hq.mcp.access.time.sleep", waits.append)
    assert load_servers(tmp_path / "inventory.json") == ([], problem)
    assert len(calls) == reads and waits == [0.05] * (reads - 1)


@pytest.mark.parametrize(
    "server_id, username, leak",
    [(ID_A, "op$erator", "op$erator"), ("server-7f", "admin", "server-7f")],
    ids=["bad-username", "bad-id"],
)
def test_load_servers_keeps_usernames_and_server_ids_out_of_the_problem(tmp_path, caplog, server_id, username, leak):
    # 문제 글자는 Claude에게 간다. InventoryError의 글에는 검사에 걸린 ID나 서버 id가 그대로 들어 있다
    item = {"id": server_id, "name": "웹", "host": "10.0.1.21", "username": username}
    write(tmp_path / "inventory.json", {"version": 1, "settings": {"admin_ips": []}, "servers": [item]})
    with caplog.at_level(logging.WARNING, logger="hq.mcp.access"):
        servers, problem = load_servers(tmp_path / "inventory.json")
    assert servers == [] and problem == UNREADABLE
    assert username not in problem and server_id not in problem
    assert leak in caplog.text  # 자세한 이유는 로그에만 남는다


def test_a_broken_settings_file_has_a_summary_with_only_the_file_name(tmp_path):
    (tmp_path / "f").write_text("{broken", encoding="utf-8")
    broken = load_access(tmp_path / "f")
    assert broken.summary == "사용 설정 파일(f)의 내용이 정해진 모양이 아니어서 모든 서버를 끈 것으로 봅니다."
    assert str(tmp_path) in broken.problem  # 사람에게 보이는 이유는 그대로다
    folder = load_access(tmp_path)  # 폴더라 읽을 수 없다
    assert folder.summary == f"사용 설정 파일({tmp_path.name})을 읽을 수 없어 모든 서버를 끈 것으로 봅니다."
    assert load_access(tmp_path / "없음").summary is None


INVENTORY_MISSING = "서버 목록 파일(inventory.json)이 없습니다. HQ에서 서버를 먼저 등록하세요."
INVENTORY_UNREADABLE = "서버 목록 파일(inventory.json)을 읽을 수 없습니다. HQ 창을 열어 확인하세요."
INVENTORY_MALFORMED = "서버 목록 파일(inventory.json)의 내용이 정해진 모양이 아닙니다. HQ 창을 열어 확인하세요."


@pytest.mark.parametrize(
    "cause, summary",
    [
        (PermissionError(5, "Access is denied"), INVENTORY_UNREADABLE),  # 계속 잠겨 있다
        (OSError(22, "Invalid argument"), INVENTORY_UNREADABLE),
        (ValueError("버전 2"), INVENTORY_MALFORMED),
        (TypeError("목록이 아님"), INVENTORY_MALFORMED),
    ],
    ids=["locked", "os-error", "value", "type"],
)
def test_the_server_list_summary_gives_a_fixed_cause_without_paths(tmp_path, monkeypatch, cause, summary):
    (tmp_path / "inventory.json").write_text("{}", encoding="utf-8")

    def failing(self):
        raise InventoryError(f"서버 목록 파일을 읽을 수 없어 저장을 막았습니다: {tmp_path}") from cause

    monkeypatch.setattr(Inventory, "load", failing)
    monkeypatch.setattr("hq.mcp.access.time.sleep", lambda seconds: None)
    listed = load_server_list(tmp_path / "inventory.json")
    assert (listed.servers, listed.summary) == ([], summary) and listed.problem
    assert load_servers(tmp_path / "inventory.json") == ([], listed.problem)  # 사람에게 보이는 이유는 그대로다


def test_the_server_list_summary_of_a_missing_or_good_file(tmp_path):
    missing = load_server_list(tmp_path / "inventory.json")
    assert missing.summary == INVENTORY_MISSING and str(tmp_path) in missing.problem
    added = Inventory(tmp_path / "inventory.json")
    added.add({"name": "웹", "host": "10.0.1.21", "username": "admin"})
    added.save()
    assert load_server_list(tmp_path / "inventory.json") == (added.servers, None, None)


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "hq" / "mcp-access.json"
    save_access(path, Access())
    access = Access(enabled=((ID_A, "10.0.1.21"), (ID_B, "web01")), never=("10.0.1.99", "192.0.2.7"))
    save_access(path, access)  # 있는 파일을 바꿔치기한다
    assert load_access(path) == access
    assert [p.name for p in path.parent.iterdir()] == ["mcp-access.json"]  # tmp가 남지 않는다


def test_save_writes_the_forms_that_load_and_usable_compare(tmp_path):
    path = tmp_path / "mcp-access.json"
    save_access(path, Access(enabled=((ID_A, "WEB01"),), never=("::ffff:10.0.1.21",)))
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "enabled": [{"id": ID_A, "host": "web01"}],
        "never": ["10.0.1.21"],
    }


def test_save_refuses_a_never_value_that_is_not_an_ip_and_leaves_the_file(tmp_path):
    # 그대로 쓰면 다음에 읽을 때 파일이 깨진 것으로 보여 모든 서버가 꺼지고 저장도 막힌다
    path = tmp_path / "mcp-access.json"
    save_access(path, Access(enabled=((ID_A, "10.0.1.21"),), never=("10.0.1.99",)))
    before = path.read_bytes()
    with pytest.raises(AccessError, match="web01"):
        save_access(path, Access(never=("10.0.1.99", "web01")))
    assert path.read_bytes() == before and [p.name for p in tmp_path.iterdir()] == ["mcp-access.json"]
