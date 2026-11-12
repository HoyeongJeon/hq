"""profileguard의 확인: 임시 프로필 픽스처가 돌지 않았으면 대화형 셸을 띄우지 않고 시험을 실패시킨다.

여기의 시험은 대화형 셸을 띄우지 않는다. 실제 프로필로 바꿔 둔 동안 띄우는 길(run_pty, make)은 띄우는 함수를 가짜로
바꿔 두거나 아무것도 띄우지 않는 단계에서 멈춘다. 사용자의 실제 프로필 아래는 읽지도 쓰지도 않는다.
"""

import importlib
import re
import subprocess
import winreg

import profileguard
import ptyrun
import pytest
from profileguard import require_temp_profile
from ptyrun import run_pty

pytestmark = pytest.mark.integration

PROFILE_LIST = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList"


def profile_from_registry() -> str:
    """대조용 실제 프로필 폴더: whoami가 알려 주는 SID로 레지스트리 ProfileList를 읽는다. 환경 변수와 프로필 API를
    거치지 않으므로 guard가 쓰는 값과 따로 얻은 값이다."""
    out = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    ).stdout
    sid = re.findall(r"S-1-5-[\d-]+", out.decode("ascii", errors="replace"))[-1]
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"{PROFILE_LIST}\{sid}") as key:
        value, kind = winreg.QueryValueEx(key, "ProfileImagePath")
    return winreg.ExpandEnvironmentStrings(value) if kind == winreg.REG_EXPAND_SZ else value


@pytest.mark.parametrize("spell", [str, lambda path: path.upper() + "\\", lambda path: path.replace("\\", "/")])
def test_the_real_profile_is_refused_however_it_is_written(monkeypatch, spell):
    monkeypatch.setenv("USERPROFILE", spell(profile_from_registry()))
    with pytest.raises(RuntimeError, match="임시 프로필"):
        require_temp_profile()


def test_a_missing_userprofile_is_refused(monkeypatch):
    # 임시 프로필 픽스처는 언제나 USERPROFILE을 둔다. 없으면 그 픽스처가 돌지 않은 것이다
    monkeypatch.delenv("USERPROFILE")
    with pytest.raises(RuntimeError, match="임시 프로필"):
        require_temp_profile()


def test_a_temporary_profile_passes(monkeypatch, temp_appdata, tmp_path):
    assert require_temp_profile() is None  # temp_appdata가 둔 임시 프로필
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert require_temp_profile() is None


def test_the_guard_does_not_depend_on_when_it_was_imported(monkeypatch):
    # 임시 프로필이 쓰이는 동안 모듈을 다시 읽어도 실제 프로필은 Windows에서 그때그때 얻는다
    importlib.reload(profileguard)
    monkeypatch.setenv("USERPROFILE", profile_from_registry())
    with pytest.raises(RuntimeError, match="임시 프로필"):
        profileguard.require_temp_profile()


def test_run_pty_starts_nothing_with_the_real_profile(monkeypatch):
    started = []

    def spawn(*args):
        started.append(args)
        pytest.fail("실제 프로필로 셸을 띄우려 했다")

    monkeypatch.setattr(ptyrun, "spawn_conpty", spawn)  # 먼저 바꿔 두어 무엇도 실제로 띄우지 않게 한다
    monkeypatch.setenv("USERPROFILE", profile_from_registry())
    with pytest.raises(RuntimeError, match="임시 프로필"):
        run_pty(["powershell.exe", "-NoLogo", "-NoProfile", "-NoExit"])
    assert started == []
