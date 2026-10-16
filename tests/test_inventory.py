import json
import os
from pathlib import Path

import pytest

from hq.inventory import Inventory, InventoryError, clean_fields
from hq.validation import ValidationError

WEB = {"name": "WEB-PRD-01", "nickname": "웹01", "host": "10.0.1.21", "group": "운영", "username": "Administrator",
       "memo": "IIS 8.5\r\n매주 화 02시 백업"}
DB = {"name": "DB-01", "host": "10.0.1.31", "username": "opsadmin"}


def make(tmp_path):
    inventory = Inventory(tmp_path / "inventory.json")
    inventory.load()
    return inventory


def test_clean_fields_normalizes_and_fills_blanks():
    fields = clean_fields({**WEB, "name": "  WEB-PRD-01 ", "nickname": None})
    assert fields == {"name": "WEB-PRD-01", "nickname": "", "group": "운영", "memo": "IIS 8.5\n매주 화 02시 백업",
                      "host": "10.0.1.21", "username": "Administrator"}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"name": " "}, "이름"),
        ({"nickname": "가" * 51}, "별명"),
        ({"group": "가" * 51}, "그룹"),
        ({"memo": "가" * 4001}, "메모"),
        ({"host": "bad host"}, "호스트"),
        ({"username": "admin'"}, "ID"),
    ],
)
def test_clean_fields_rejects(change, message):
    with pytest.raises(ValidationError, match=message):
        clean_fields({**WEB, **change})


def test_add_get_and_find_by_host(tmp_path):
    inventory = make(tmp_path)
    server = inventory.add(WEB)
    assert len(server.id) == 32
    assert server.display_name == "웹01"
    assert inventory.get(server.id) == server
    assert inventory.find_by_host("10.0.1.21 ").id == server.id
    assert inventory.find_by_host("10.0.1.99") is None


def test_returned_servers_are_copies(tmp_path):
    inventory = make(tmp_path)
    server = inventory.add(WEB)
    server.name = "바꿈"
    inventory.servers[0].name = "바꿈"
    assert inventory.get(server.id).name == "WEB-PRD-01"


def test_display_name_falls_back_to_name(tmp_path):
    assert make(tmp_path).add(DB).display_name == "DB-01"


def test_host_must_be_unique_ignoring_case(tmp_path):
    inventory = make(tmp_path)
    inventory.add({**DB, "host": "web01.corp.local"})
    with pytest.raises(ValidationError, match="이미 등록된"):
        inventory.add({**WEB, "host": "WEB01.corp.local"})


def test_update_keeps_id_and_allows_its_own_host(tmp_path):
    inventory = make(tmp_path)
    web = inventory.add(WEB)
    db = inventory.add(DB)
    updated = inventory.update(web.id, {**WEB, "nickname": "새별명"})
    assert updated.id == web.id and updated.nickname == "새별명"
    with pytest.raises(ValidationError):
        inventory.update(web.id, {**WEB, "host": db.host})


def test_remove_and_missing_ids(tmp_path):
    inventory = make(tmp_path)
    server = inventory.add(WEB)
    assert inventory.remove(server.id).id == server.id
    with pytest.raises(KeyError):
        inventory.get(server.id)
    with pytest.raises(KeyError):
        inventory.remove(server.id)


def test_save_and_load_round_trip(tmp_path):
    inventory = make(tmp_path)
    web = inventory.add(WEB)
    inventory.set_admin_ips(["10.0.0.50", " ", "10.0.0.51", "10.0.0.50"])
    inventory.save()
    again = make(tmp_path)
    assert again.servers == [web]
    assert again.admin_ips == ["10.0.0.50", "10.0.0.51"]
    data = json.loads((tmp_path / "inventory.json").read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert "password" not in json.dumps(data)


def test_second_save_keeps_the_previous_file_as_bak(tmp_path):
    inventory = make(tmp_path)
    inventory.add(WEB)
    inventory.save()
    first = (tmp_path / "inventory.json").read_text(encoding="utf-8")
    inventory.add(DB)
    inventory.save()
    assert (tmp_path / "inventory.json.bak").read_text(encoding="utf-8") == first
    assert not (tmp_path / "inventory.json.tmp").exists()


def test_save_puts_the_whole_temp_file_on_disk_before_replacing(tmp_path, monkeypatch):
    calls = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd):
        calls.append(("fsync", os.fstat(fd).st_size))
        real_fsync(fd)

    def replace(src, dst):
        calls.append(("replace", Path(src).name))
        real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    inventory = make(tmp_path)
    inventory.add(WEB)
    inventory.save()
    size = (tmp_path / "inventory.json").stat().st_size
    assert calls == [("fsync", size), ("replace", "inventory.json.tmp")]


def test_missing_file_means_empty(tmp_path):
    inventory = make(tmp_path)
    assert inventory.servers == [] and inventory.admin_ips == []


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        json.dumps({"version": 2, "servers": []}),
        json.dumps({"version": 1, "servers": [{"id": "x", "name": "a", "host": "10.0.0.1", "username": "u"}]}),
        json.dumps({"version": 1, "servers": [
            {"id": "a" * 32, "name": "a", "host": "10.0.0.1", "username": "u"},
            {"id": "b" * 32, "name": "b", "host": "10.0.0.1", "username": "u"}]}),
    ],
)
def test_broken_file_blocks_saving_and_stays_untouched(tmp_path, content):
    path = tmp_path / "inventory.json"
    path.write_text(content, encoding="utf-8")
    inventory = Inventory(path)
    with pytest.raises(InventoryError, match=r"inventory\.json\.bak"):
        inventory.load()
    with pytest.raises(InventoryError):
        inventory.save()
    assert path.read_text(encoding="utf-8") == content


def test_admin_ips_must_be_ipv4(tmp_path):
    with pytest.raises(ValidationError):
        make(tmp_path).set_admin_ips(["10.0.0"])
