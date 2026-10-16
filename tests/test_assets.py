import struct
from pathlib import Path

ICON = Path(__file__).resolve().parents[1] / "src" / "hq" / "assets" / "hq.ico"


def test_icon_is_a_windows_ico():
    assert ICON.read_bytes()[:4] == b"\x00\x00\x01\x00"


def test_icon_has_every_size_windows_asks_for():
    # 제목 표시줄(16), 작업 표시줄(24·32), 탐색기 큰 아이콘(48~256)
    data = ICON.read_bytes()
    (count,) = struct.unpack_from("<H", data, 4)
    sizes = {data[6 + 16 * i] or 256 for i in range(count)}  # 디렉터리 항목의 너비. 0은 256
    assert sizes == {16, 24, 32, 48, 64, 128, 256}
