import json

import pytest

from hq.verify import main, username_check_script

USERS = [".\\Administrator", "WEB01\\Administrator"]
SECRET = r"C:\t\s.dpapi"


def test_rejects_a_bad_host_before_asking_for_a_password(monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda *_: pytest.fail("비밀번호를 묻기 전에 막아야 한다"))
    with pytest.raises(SystemExit) as exc:
        main(["--host", "bad host", "--user", "Administrator"])
    assert exc.value.code == 2


def test_username_check_script_carries_values_only_in_the_json_literal():
    # HQ가 띄우는 스크립트에는 FromBase64String을 넣지 않는다.
    # AhnLab V3가 이것과 Invoke-Command가 함께 든 명령의 실행을 막는다.
    script = username_check_script("10.0.1.21", USERS, SECRET)
    assert "FromBase64String" not in script
    marker = "$p = '"
    start = script.index(marker) + len(marker)
    inner = script[start : script.index("'", start)]
    assert all(0x20 <= ord(c) <= 0x7E for c in inner), inner
    assert json.loads(inner) == {"host": "10.0.1.21", "users": USERS, "secret": SECRET}
    assert script.count(inner) == 1
    rest = script.replace(f"'{inner}'", "")
    for value in ("10.0.1.21", "Administrator", "WEB01", "dpapi"):
        assert value not in rest, value
