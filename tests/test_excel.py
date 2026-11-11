import logging
import re
import traceback
import warnings
import zipfile
from datetime import date, datetime, time, timedelta

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


def rewrite_sheet(source, target, edit):
    """xlsx 안의 첫 시트 XML만 edit(bytes) -> bytes로 바꿔 새 파일로 쓴다(다른 도구가 만든 이상한 파일 흉내)."""
    with zipfile.ZipFile(source) as zin, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = edit(data)
            zout.writestr(item, data)
    return target


def fresh(tmp_path):
    inventory = Inventory(tmp_path / "inventory.json")
    inventory.load()
    return inventory, SecretStore(tmp_path / "secrets")


def password_of(secrets, server_id):
    return _unprotect(bytes.fromhex(secrets.path_for(server_id).read_text(encoding="ascii"))).decode("utf-16-le")


NOT_TEXT_PW = (
    "PW 칸을 엑셀이 글자가 아닌 값으로 바꿨습니다(소수, 날짜, TRUE/FALSE, 15자리를 넘는 숫자, 오류 값). "
    "칸 서식을 '텍스트'로 바꾸고 다시 입력하세요."
)


def rows_with_passwords(passwords):
    """PW 칸만 다른 서버 행들(호스트는 10.0.2.1부터)."""
    return [[f"S{n}", None, f"10.0.2.{n}", None, "opsadmin", pw, None] for n, pw in enumerate(passwords, start=1)]


def with_raw_numbers(tmp_path, rows, raw):
    """raw: {자리표시 정수: 시트 XML의 <v> 글자}. openpyxl은 12345678.0을 정수로 적으므로, 소수점이나 지수로 적힌
    숫자 칸(읽으면 float)은 이렇게 만든다."""
    source = make_xlsx(tmp_path / "src.xlsx", rows)

    def edit(data):
        for placeholder, text in raw.items():
            assert data.count(f"<v>{placeholder}</v>".encode()) == 1
            data = data.replace(f"<v>{placeholder}</v>".encode(), f"<v>{text}</v>".encode())
        return data

    return rewrite_sheet(source, tmp_path / "a.xlsx", edit)


def replace_once(data, old, new):
    assert data.count(old) == 1, old
    return data.replace(old, new)


def password_cells(path):
    """openpyxl이 PW 칸을 어떤 값으로 돌려주는지(read_rows와 같은 방식으로 연다)."""
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        return [row[5] for row in workbook["서버"].iter_rows(min_row=2, values_only=True)]
    finally:
        workbook.close()


def test_template_round_trips_with_text_format(tmp_path):
    path = tmp_path / "양식.xlsx"
    write_template(path)
    assert read_rows(path) == []
    workbook = load_workbook(path)
    assert workbook.sheetnames == ["서버", "설명"]
    assert [c.value for c in workbook["서버"][1]] == HEADERS
    assert workbook["서버"]["F2"].number_format == "@"


def test_template_help_shows_how_to_write_a_local_and_a_domain_id(tmp_path):
    # 도메인에 가입된 서버에서 접두 없는 계정은 서버의 로컬 계정으로 확인되어 거부된다(체크포인트 D)
    path = tmp_path / "양식.xlsx"
    write_template(path)
    lines = [row[0] for row in load_workbook(path)["설명"].iter_rows(values_only=True) if row[0]]
    id_line = next(line for line in lines if line.startswith("4. ID:"))
    for part in ("로컬 계정", "Administrator", "도메인 계정", "도메인\\계정", "CONTOSO\\opsadmin"):
        assert part in id_line, id_line


def test_template_help_says_not_to_change_the_password_cell_format(tmp_path):
    # 엑셀이 정수로 바꾼 값(0012 → 12)은 직접 친 정수와 구별할 수 없으므로 설명으로 막는다
    path = tmp_path / "양식.xlsx"
    write_template(path)
    lines = [row[0] for row in load_workbook(path)["설명"].iter_rows(values_only=True) if row[0]]
    assert lines[6].startswith("6. ")
    assert lines[7] == (
        "7. PW 칸의 텍스트 서식을 바꾸지 마세요. 숫자 서식이 되면 엑셀이 값을 바꿉니다(예: 0012 → 12). "
        "정수로 바뀐 값은 HQ가 알아챌 수 없습니다."
    )


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


def test_unreadable_cell_never_leaks_its_text(tmp_path):
    source = make_xlsx(tmp_path / "src.xlsx", [["A", None, "10.0.0.1", None, "u", "pw", None]])
    numeric_secret = b'<c r="F2" t="n"><v>S3cretPW</v></c>'  # 숫자 칸이라고 적혀 있는데 글자가 들어 있음
    path = rewrite_sheet(source, tmp_path / "a.xlsx",
                         lambda data: re.sub(rb'<c r="F2".*?</c>', numeric_secret, data, count=1))
    with pytest.raises(ExcelError, match="내용을 읽을 수 없습니다") as info:
        read_rows(path)
    error = info.value
    assert "S3cretPW" not in str(error)
    assert error.__cause__ is None and error.__context__ is None
    assert "S3cretPW" not in "".join(traceback.format_exception(error))


def test_broken_sheet_xml_is_rejected(tmp_path):
    source = make_xlsx(tmp_path / "src.xlsx", [["A", None, "10.0.0.1", None, "u", "pw", None]])
    path = rewrite_sheet(source, tmp_path / "a.xlsx", lambda data: data[: len(data) // 2])
    with pytest.raises(ExcelError, match="내용을 읽을 수 없습니다"):
        read_rows(path)


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


def test_password_cells_excel_turned_into_other_values_are_error_rows(tmp_path):
    # 서식이 텍스트가 아닌 PW 칸은 엑셀이 친 글자를 바꿔 둔다(TRUE, 날짜, 1234.50 → 1234.5). 그대로 저장하면
    # 접속할 때에야 로그인 실패로 드러나고, 되풀이하면 계정이 잠길 수 있다
    values = [True, False, datetime(2031, 7, 19, 8, 30), date(2031, 7, 19), time(8, 30), timedelta(hours=5), 1234.5]
    path = make_xlsx(tmp_path / "a.xlsx", rows_with_passwords(values))
    assert [type(v) for v in password_cells(path)] == [bool, bool, datetime, datetime, time, timedelta, float]
    preview = build_preview(path, fresh(tmp_path)[0])
    assert [(r.status, r.error, r.password) for r in preview.rows] == [("error", NOT_TEXT_PW, "")] * len(values)


def test_whole_number_passwords_are_kept_only_while_excel_holds_every_digit(tmp_path):
    # 엑셀은 유효 숫자 15자리까지만 정확히 둔다. 그보다 긴 수는 친 숫자와 다른 숫자가 된다
    rows = rows_with_passwords([12345678, 777001, 10**15 - 1, 777002, -12, "  pw  ", 10**15, -(10**15), 777003, 1e19])
    path = with_raw_numbers(tmp_path, rows, {777001: "12345678.0", 777002: "999999999999999.0", 777003: "1E+15"})
    assert [type(v) for v in password_cells(path)] == [int, float, int, float, int, str, int, int, float, float]
    preview = build_preview(path, fresh(tmp_path)[0])
    kept = [
        ("new", "", "12345678"),
        ("new", "", "12345678"),
        ("new", "", "999999999999999"),
        ("new", "", "999999999999999"),
        ("new", "", "-12"),
        ("new", "", "  pw  "),
    ]
    assert [(r.status, r.error, r.password) for r in preview.rows] == kept + [("error", NOT_TEXT_PW, "")] * 4


def test_password_cells_holding_an_excel_error_value_are_error_rows(tmp_path):
    # 수식 오류(#DIV/0! 등)도 openpyxl은 글자로 돌려준다. 칸의 형(data_type "e")으로만 글자로 친 값과 구별된다
    rows = rows_with_passwords(["#DIV/0!", "#DIV/0!", "#N/A"])
    rows[2][6] = "#REF!"  # PW가 아닌 칸의 오류 값은 지금처럼 글자로 읽는다
    source = make_xlsx(tmp_path / "src.xlsx", rows)  # openpyxl은 "#DIV/0!", "#N/A" 같은 글자를 오류 칸으로 적는다

    def edit(data):
        formula = b'<c r="F3" t="e"><f>1/0</f><v>#DIV/0!</v></c>'  # 엑셀이 저장한 수식 칸
        typed = b'<c r="F4" t="inlineStr"><is><t>#N/A</t></is></c>'  # 텍스트 칸에 친 #N/A
        data = replace_once(data, b'<c r="F3" t="e"><v>#DIV/0!</v></c>', formula)
        return replace_once(data, b'<c r="F4" t="e"><v>#N/A</v></c>', typed)

    path = rewrite_sheet(source, tmp_path / "a.xlsx", edit)
    assert password_cells(path) == ["#DIV/0!", "#DIV/0!", "#N/A"]  # 값만 보면 셋 다 같은 글자다
    inventory, secrets = fresh(tmp_path)
    preview = build_preview(path, inventory)
    assert [(r.status, r.error, r.password) for r in preview.rows] == [
        ("error", NOT_TEXT_PW, ""),
        ("error", NOT_TEXT_PW, ""),
        ("new", "", "#N/A"),
    ]
    assert preview.rows[2].fields["memo"] == "#REF!"
    result = apply_preview(preview, inventory, secrets, overwrite=True)
    assert (result.added, result.updated, result.skipped, result.errors) == (1, 0, 0, 2)
    [server] = inventory.servers
    assert server.host == "10.0.2.3" and password_of(secrets, server.id) == "#N/A"


def test_an_out_of_range_date_password_cell_is_an_error_row_and_its_number_is_never_printed(tmp_path, capfd):
    # 날짜 서식 칸의 수가 날짜 범위를 넘으면 openpyxl은 그 수를 넣은 경고를 내고 "#VALUE!" 오류 값을 돌려준다
    path = make_xlsx(tmp_path / "a.xlsx", rows_with_passwords([98765432]))
    workbook = load_workbook(path)
    workbook["서버"]["F2"].number_format = "yyyy-mm-dd"
    workbook.save(path)
    with warnings.catch_warnings(record=True) as raw:
        warnings.simplefilter("always")
        assert password_cells(path) == ["#VALUE!"]
    assert any("98765432" in str(w.message) for w in raw)  # openpyxl만으로 읽으면 수가 경고에 찍힌다

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        preview = build_preview(path, fresh(tmp_path)[0])
    assert [str(w.message) for w in caught] == []
    assert [(r.status, r.error, r.password) for r in preview.rows] == [("error", NOT_TEXT_PW, "")]
    out, err = capfd.readouterr()
    assert "98765432" not in out + err + repr(preview.rows)


def test_a_row_with_only_a_non_text_password_is_an_error_row_not_a_blank_one(tmp_path):
    path = make_xlsx(tmp_path / "a.xlsx", [[None, None, None, None, None, True, None]])
    assert read_rows(path) == [(2, ["", "", "", "", "", None, ""])]  # None: 글자로 믿을 수 없는 PW 칸
    preview = build_preview(path, fresh(tmp_path)[0])
    assert [(r.row, r.status, r.password) for r in preview.rows] == [(2, "error", "")]


def test_fields_are_checked_before_the_password_cell(tmp_path):
    path = make_xlsx(
        tmp_path / "a.xlsx",
        [
            ["GOOD", None, "10.0.1.21", None, "opsadmin", 1234.5, None],
            ["BAD", None, "bad host", None, "opsadmin", 1234.5, None],
        ],
    )
    good, bad = build_preview(path, fresh(tmp_path)[0]).rows
    assert (good.status, good.error) == ("error", NOT_TEXT_PW)
    assert bad.status == "error" and "호스트" in bad.error, bad.error  # 같은 행의 PW 칸보다 칸 검사 문구가 먼저


def test_apply_never_saves_a_non_text_password(tmp_path):
    inventory, secrets = fresh(tmp_path)
    old = inventory.add({"name": "WEB", "host": "10.0.1.21", "username": "Administrator"})
    secrets.save(old.id, "old-pw")
    path = make_xlsx(
        tmp_path / "a.xlsx",
        [
            ["WEB", None, "10.0.1.21", None, "Administrator", 1234.5, None],
            ["DB", None, "10.0.1.31", None, "opsadmin", True, None],
        ],
    )
    result = apply_preview(build_preview(path, inventory), inventory, secrets, overwrite=True)
    assert (result.added, result.updated, result.skipped, result.errors) == (0, 0, 0, 2)
    assert password_of(secrets, old.id) == "old-pw"
    assert [s.host for s in inventory.servers] == ["10.0.1.21"]


def test_a_non_text_password_cell_never_leaks_its_value(tmp_path, caplog):
    # PW 칸의 값은 비밀번호의 대부분일 수 있다. 오류 문구, 행 정보, 로그 어디에도 남기지 않는다
    caplog.set_level(logging.DEBUG)
    values = [98765.4321, datetime(2031, 7, 19, 8, 30), 98765432109876543]
    path = make_xlsx(tmp_path / "a.xlsx", rows_with_passwords(values))
    preview = build_preview(path, fresh(tmp_path)[0])
    assert [r.error for r in preview.rows] == [NOT_TEXT_PW] * len(values)
    shown = repr(preview.rows) + caplog.text
    for part in ("98765", "4321", "2031", "08:30", "8:30", "9876543"):
        assert part not in shown, part


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
