"""엑셀 일괄 등록: 양식 만들기, 미리보기(아직 저장하지 않음), 적용.

고른 파일은 읽기만 하고 복사본을 만들지 않는다. 미리보기 결과(비밀번호 포함)는 메모리에만
잠시 둔다(PreviewStore).
"""

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from hq.inventory import Inventory, clean_fields
from hq.secret_store import SecretStore
from hq.validation import ValidationError

HEADERS = ["이름*", "닉네임", "IP/호스트*", "그룹", "ID*", "PW*", "메모"]
_KEYS = ["name", "nickname", "host", "group", "username", "password", "memo"]
_PASSWORD_COLUMN = _KEYS.index("password")
MAX_BYTES = 5 * 1024 * 1024
TEMPLATE_ROWS = 500
PREVIEW_TTL = 600.0
HELP_LINES = [
    "HQ 서버 목록 양식",
    "1. '서버' 시트의 2행부터 서버 한 대에 한 줄씩 적습니다. 1행 머리글은 바꾸지 마세요.",
    "2. * 표시 칸(이름, IP/호스트, ID, PW)은 꼭 채웁니다.",
    "3. IP/호스트: IPv4 주소 또는 호스트 이름(영문, 숫자, 점, 하이픈).",
    "4. ID: 서버의 로컬 계정은 그대로, 도메인 계정은 도메인\\계정으로 적습니다. 예) Administrator, CONTOSO\\opsadmin",
    "5. 이미 등록된 IP/호스트는 기본으로 건너뜁니다. 미리보기에서 덮어쓰기를 고르면 이 파일의 값으로 바뀝니다.",
    "6. 이 파일에는 비밀번호가 평문으로 들어 있습니다. 등록한 뒤 바로 지우세요.",
]
_EXAMPLE = ["WEB-PRD-01", "웹01", "10.0.1.21", "운영", "Administrator", "(비밀번호)", "IIS 8.5"]


class ExcelError(Exception):
    """파일을 읽을 수 없을 때. 메시지는 화면에 그대로 보여 준다."""


def write_template(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "서버"
    sheet.append(HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in sheet.iter_rows(min_row=1, max_row=TEMPLATE_ROWS + 1, max_col=len(HEADERS)):
        for cell in row:
            cell.number_format = "@"  # 텍스트 서식: 숫자로만 된 비밀번호·IP가 숫자로 바뀌지 않게
    for letter, width in zip("ABCDEFG", [18, 14, 18, 12, 18, 18, 40], strict=True):
        sheet.column_dimensions[letter].width = width
    guide = workbook.create_sheet("설명")
    for line in HELP_LINES:
        guide.append([line])
    guide.append([])
    guide.append(["예시 (이 시트의 내용은 등록되지 않습니다)"])
    guide.append(HEADERS)
    guide.append(_EXAMPLE)
    workbook.save(path)


def _cell_text(value: Any, *, strip: bool = True) -> str:
    if value is None:
        return ""
    text = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
    return text.strip() if strip else text


def read_rows(path: Path) -> list[tuple[int, list[str]]]:
    """(엑셀 행 번호, 7칸 글자) 목록. 빈 행은 뺀다. PW 칸만 앞뒤 공백을 그대로 둔다."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ExcelError(f"파일을 열 수 없습니다: {exc}") from exc
    if size > MAX_BYTES:
        raise ExcelError("파일이 5MB보다 큽니다.")
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # openpyxl은 형식 문제마다 다른 예외를 던진다
        raise ExcelError(f"엑셀 파일(.xlsx)을 열 수 없습니다: {exc}") from exc
    result: list[tuple[int, list[str]]] | None = []
    try:
        # read_only라서 시트 XML은 여기서 읽힌다. 그래서 내용이 깨진 파일의 예외도 여기서 난다.
        sheet = workbook["서버"] if "서버" in workbook.sheetnames else workbook.worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        header = [_cell_text(v) for v in (next(rows, None) or ())]
        if header[: len(HEADERS)] != HEADERS:
            raise ExcelError("양식이 다릅니다. [양식 저장]으로 받은 파일의 1행 머리글을 그대로 두세요.")
        for number, values in enumerate(rows, start=2):
            cells = list(values or ())[: len(HEADERS)]
            cells += [None] * (len(HEADERS) - len(cells))
            texts = [_cell_text(v, strip=(i != _PASSWORD_COLUMN)) for i, v in enumerate(cells)]
            if any(t.strip() for t in texts):
                result.append((number, texts))
    except ExcelError:
        raise
    except Exception:
        # openpyxl의 예외 메시지에는 셀 글자(비밀번호일 수 있음)가 들어 있다. `from None`으로도
        # __context__에는 남으므로, except 밖에서 새로 던져 원래 예외와 아예 잇지 않는다.
        result = None
    finally:
        workbook.close()
    if result is None:
        raise ExcelError("엑셀 파일의 내용을 읽을 수 없습니다. 엑셀에서 열어 다시 저장해 보세요.")
    return result


@dataclass
class PreviewRow:
    row: int
    status: str  # "new" | "existing" | "error"
    fields: dict[str, str]
    password: str = field(default="", repr=False)
    error: str = ""


@dataclass
class Preview:
    id: str
    rows: list[PreviewRow]
    created: float


def build_preview(path: Path, inventory: Inventory, *, clock: Callable[[], float] = time.monotonic) -> Preview:
    rows: list[PreviewRow] = []
    first_row_of: dict[str, int] = {}
    for number, texts in read_rows(path):
        values = dict(zip(_KEYS, texts, strict=True))
        password = values.pop("password")
        try:
            fields = clean_fields(values)
            if not password:
                raise ValidationError("PW를 입력하세요.")
        except ValidationError as exc:
            rows.append(PreviewRow(number, "error", values, error=str(exc)))
            continue
        key = fields["host"].lower()
        if key in first_row_of:
            message = f"파일 안에서 IP/호스트가 중복됩니다({first_row_of[key]}행과 같음)."
            rows.append(PreviewRow(number, "error", fields, error=message))
            continue
        first_row_of[key] = number
        status = "existing" if inventory.find_by_host(fields["host"]) else "new"
        rows.append(PreviewRow(number, status, fields, password=password))
    return Preview(uuid.uuid4().hex, rows, clock())


@dataclass
class ApplyResult:
    added: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0


def apply_preview(preview: Preview, inventory: Inventory, secrets: SecretStore, *, overwrite: bool) -> ApplyResult:
    """오류 행은 빼고 등록한다. 등록 여부는 지금 목록 기준으로 다시 판단하고, 저장은 마지막에 한 번 한다."""
    result = ApplyResult()
    for row in preview.rows:
        if row.status == "error":
            result.errors += 1
            continue
        current = inventory.find_by_host(row.fields["host"])
        if current is not None and not overwrite:
            result.skipped += 1
            continue
        if current is None:
            server = inventory.add(row.fields)
            result.added += 1
        else:
            server = inventory.update(current.id, row.fields)
            result.updated += 1
        secrets.save(server.id, row.password)
    inventory.save()
    return result


class PreviewStore:
    """미리보기 결과를 메모리에만 잠시 둔다. 꺼내면 지워지고, ttl초가 지나도 지워진다."""

    def __init__(self, ttl: float = PREVIEW_TTL, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl
        self._clock = clock
        self._items: dict[str, Preview] = {}
        self._lock = threading.Lock()

    def put(self, preview: Preview) -> None:
        with self._lock:
            self._purge()
            self._items[preview.id] = preview

    def take(self, preview_id: str) -> Preview | None:
        with self._lock:
            self._purge()
            return self._items.pop(preview_id, None)

    def _purge(self) -> None:
        now = self._clock()
        for key in [k for k, p in self._items.items() if now - p.created > self._ttl]:
            del self._items[key]
