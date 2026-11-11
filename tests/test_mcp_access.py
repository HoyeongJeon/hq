import json
import socket
import time

import pytest

from hq.inventory import Inventory, InventoryError, Server
from hq.mcp.access import (
    Access,
    AccessError,
    canonical_ip,
    load_access,
    load_servers,
    resolve_host,
    save_access,
    target_of,
    usable,
)

ID_A, ID_B = "a" * 32, "b" * 32


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


@pytest.mark.parametrize("cause, reads", [(PermissionError(5, "Access is denied"), 5), (ValueError("버전 2"), 1)])
def test_load_servers_reads_again_only_while_the_file_is_locked(tmp_path, monkeypatch, cause, reads):
    (tmp_path / "inventory.json").write_text("{}", encoding="utf-8")
    calls, waits = [], []

    def failing(self):
        calls.append(1)
        raise InventoryError("서버 목록 파일을 읽을 수 없습니다") from cause

    monkeypatch.setattr(Inventory, "load", failing)
    monkeypatch.setattr("hq.mcp.access.time.sleep", waits.append)
    assert load_servers(tmp_path / "inventory.json") == ([], "서버 목록 파일을 읽을 수 없습니다")
    assert len(calls) == reads and waits == [0.05] * (reads - 1)


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "hq" / "mcp-access.json"
    save_access(path, Access())
    access = Access(enabled=((ID_A, "10.0.1.21"), (ID_B, "web01")), never=("10.0.1.99", "192.0.2.7"))
    save_access(path, access)  # 있는 파일을 바꿔치기한다
    assert load_access(path) == access
    assert [p.name for p in path.parent.iterdir()] == ["mcp-access.json"]  # tmp가 남지 않는다
