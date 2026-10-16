import base64
import json
import subprocess
from pathlib import Path

import pytest

from hq.verify import main, username_check_script

USERS = [".\\Administrator", "WEB01\\Administrator"]
SECRET = r"C:\t\s.dpapi"
TRY_USERS = ["--host", "10.0.1.21", "--user", "Administrator", "--try-users", "Administrator"]


def _literal(script: str) -> str:
    marker = "$p = '"
    start = script.index(marker) + len(marker)
    return script[start : script.index("'", start)]


def _work_dirs(local_appdata: Path) -> list[Path]:
    return sorted((local_appdata / "hq").glob("hq-verify-*"))


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
    inner = _literal(script)
    assert all(0x20 <= ord(c) <= 0x7E for c in inner), inner
    assert json.loads(inner) == {"host": "10.0.1.21", "users": USERS, "secret": SECRET}
    assert script.count(inner) == 1
    rest = script.replace(f"'{inner}'", "")
    for value in ("10.0.1.21", "Administrator", "WEB01", "dpapi"):
        assert value not in rest, value


def test_temp_secret_is_written_under_the_data_dir_and_removed_afterwards(monkeypatch, tmp_path):
    # TMPDIR가 Everyone 전체 권한 폴더일 수 있다(이 PC: C:\Users\Public\...).
    # 비밀번호 파일은 %LOCALAPPDATA%\hq에만 둔다.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("getpass.getpass", lambda *_: "test-password")
    seen: list[Path] = []

    def fake_run(argv, **_):
        script = base64.b64decode(argv[-1]).decode("utf-16-le")
        secret = Path(json.loads(_literal(script))["secret"])
        assert secret.is_file()
        seen.append(secret)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr("hq.verify.subprocess.run", fake_run)
    assert main(TRY_USERS) == 0
    assert len(seen) == 1
    assert seen[0].is_relative_to(tmp_path / "hq"), seen[0]
    assert _work_dirs(tmp_path) == []


@pytest.mark.parametrize("failure", ["abort-at-password", "launch-refused"])
def test_temp_folder_is_removed_when_the_run_stops_early(monkeypatch, tmp_path, failure):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    created: list[Path] = []

    def password(*_):
        created.extend(_work_dirs(tmp_path))
        if failure == "abort-at-password":
            raise KeyboardInterrupt
        return "test-password"

    def refused(*_, **__):
        raise PermissionError(13, "액세스가 거부되었습니다")

    monkeypatch.setattr("getpass.getpass", password)
    monkeypatch.setattr("hq.verify.subprocess.run", refused)
    with pytest.raises(KeyboardInterrupt if failure == "abort-at-password" else PermissionError):
        main(TRY_USERS)
    assert len(created) == 1
    assert _work_dirs(tmp_path) == []
