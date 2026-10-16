import pytest
from openpyxl import Workbook, load_workbook

from hq.excel import (
    HEADERS,
    MAX_BYTES,
    ExcelError,
    Preview,
    PreviewStore,
    apply_preview,
    build_preview,
    read_rows,
    write_template,
)
from hq.inventory import Inventory
from hq.secret_store import SecretStore, _unprotect


def make_xlsx(path, rows, header=HEADERS):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "서버"
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    return path


def fresh(tmp_path):
    inventory = Inventory(tmp_path / "inventory.json")
    inventory.load()
    return inventory, SecretStore(tmp_path / "secrets")


def password_of(secrets, server_id):
    return _unprotect(bytes.fromhex(secrets.path_for(server_id).read_text(encoding="ascii"))).decode("utf-16-le")


def test_template_round_trips_with_text_format(tmp_path):
    path = tmp_path / "양식.xlsx"
    write_template(path)
    assert read_rows(path) == []
    workbook = load_workbook(path)
    assert workbook.sheetnames == ["서버", "설명"]
    assert [c.value for c in workbook["서버"][1]] == HEADERS
    assert workbook["서버"]["F2"].number_format == "@"


def test_numbers_become_text_and_only_the_password_keeps_spaces(tmp_path):
    path = make_xlsx(tmp_path / "a.xlsx", [[" WEB ", None, "10.0.1.21", None, "Administrator", 12345678, None],
                                           ["DB", None, "10.0.1.31", None, "opsadmin", 1234.0, None],
                                           ["APP", None, "10.0.1.41", None, "opsadmin", " p w ", None]])
    rows = read_rows(path)
    assert rows[0] == (2, ["WEB", "", "10.0.1.21", "", "Administrator", "12345678", ""])
    assert rows[1][1][5] == "1234"
    assert rows[2][1][5] == " p w "


def test_blank_rows_are_skipped_but_row_numbers_stay_true(tmp_path):
    path = make_xlsx(tmp_path / "a.xlsx", [["A", None, "10.0.0.1", None, "u", "p", None], [None] * 7,
                                           ["B", None, "10.0.0.2", None, "u", "p", None]])
    assert [number for number, _ in read_rows(path)] == [2, 4]


def test_changed_header_is_rejected(tmp_path):
    path = make_xlsx(tmp_path / "a.xlsx", [], header=["이름", "IP"])
    with pytest.raises(ExcelError, match="양식이 다릅니다"):
        read_rows(path)


def test_too_big_or_not_xlsx_is_rejected(tmp_path):
    big = tmp_path / "big.xlsx"
    big.write_bytes(b"0" * (MAX_BYTES + 1))
    with pytest.raises(ExcelError, match="5MB"):
        read_rows(big)
    text = tmp_path / "text.xlsx"
    text.write_text("hello", encoding="utf-8")
    with pytest.raises(ExcelError):
        read_rows(text)


def test_preview_marks_new_existing_and_errors(tmp_path):
    inventory, _ = fresh(tmp_path)
    inventory.add({"name": "OLD", "host": "10.0.1.21", "username": "Administrator"})
    path = make_xlsx(tmp_path / "a.xlsx", [
        ["WEB", "웹01", "10.0.1.21", "운영", "Administrator", "pw1", None],
        ["DB", None, "10.0.1.31", None, "opsadmin", "pw2", None],
        ["NOPW", None, "10.0.1.41", None, "opsadmin", None, None],
        ["BAD", None, "bad host", None, "opsadmin", "pw", None],
        ["DUP", None, "10.0.1.31", None, "opsadmin", "pw", None],
    ])
    preview = build_preview(path, inventory)
    assert [(r.row, r.status) for r in preview.rows] == [(2, "existing"), (3, "new"), (4, "error"), (5, "error"),
                                                         (6, "error")]
    assert "PW" in preview.rows[2].error
    assert "호스트" in preview.rows[3].error
    assert "3행" in preview.rows[4].error
    assert preview.rows[0].password == "pw1"
    assert "pw1" not in repr(preview)


def test_apply_skips_existing_unless_overwrite(tmp_path):
    inventory, secrets = fresh(tmp_path)
    old = inventory.add({"name": "OLD", "nickname": "옛별명", "host": "10.0.1.21", "username": "Administrator"})
    path = make_xlsx(tmp_path / "a.xlsx", [["WEB", None, "10.0.1.21", None, "Administrator", "new-pw", None],
                                           ["DB", None, "10.0.1.31", None, "opsadmin", "db-pw", None],
                                           ["BAD", None, "bad host", None, "opsadmin", "pw", None]])
    result = apply_preview(build_preview(path, inventory), inventory, secrets, overwrite=False)
    assert (result.added, result.updated, result.skipped, result.errors) == (1, 0, 1, 1)
    assert not secrets.has(old.id)

    result = apply_preview(build_preview(path, inventory), inventory, secrets, overwrite=True)
    assert (result.added, result.updated, result.skipped, result.errors) == (0, 2, 0, 1)
    updated = inventory.get(old.id)
    assert updated.name == "WEB" and updated.nickname == ""  # 빈 칸은 빈 값이 된다
    assert password_of(secrets, old.id) == "new-pw"

    reloaded = Inventory(tmp_path / "inventory.json")
    reloaded.load()
    assert {s.host for s in reloaded.servers} == {"10.0.1.21", "10.0.1.31"}


def test_preview_store_forgets_after_take_or_timeout():
    now = [0.0]
    store = PreviewStore(ttl=600, clock=lambda: now[0])
    first = Preview("a" * 32, [], created=0.0)
    store.put(first)
    assert store.take(first.id) is first
    assert store.take(first.id) is None
    store.put(Preview("b" * 32, [], created=0.0))
    now[0] = 601.0
    assert store.take("b" * 32) is None
