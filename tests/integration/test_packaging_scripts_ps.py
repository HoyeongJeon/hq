"""exe 묶기 스크립트(scripts/build.ps1, scripts/make-shortcut.ps1)를 Windows PowerShell 5.1로 확인한다.

build.ps1은 가짜 uv로 돌린다(진짜 테스트·PyInstaller는 돌리지 않는다). make-shortcut.ps1은 실제로 복사하고
바탕화면 바로가기를 바꾸므로 통째로 돌리지 않는다. 문법을 보고, 쓰기 권한 검사 부분만 떼어 tmp_path의 폴더에 돌린다.
"""

import re
import subprocess
from pathlib import Path

import pytest
from ps import parse_errors, run_ps

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
# 받은 하위 명령(pytest, pyinstaller)을 적고, PyInstaller처럼 stderr에 한 줄 쓴 뒤 HQ_FAKE_FAIL과 같으면 1로 끝난다
FAKE_UV = (
    "@echo off\r\n"
    '>>"%HQ_FAKE_CALLS%" echo %2\r\n'
    "echo 433 INFO: fake %2 1>&2\r\n"
    'if "%2"=="%HQ_FAKE_FAIL%" exit /b 1\r\n'
    "exit /b 0\r\n"
)
# 사용자가 셸에서 앞 명령이 성공한 뒤 `& scripts\build.ps1 *> build.log`를 친 것처럼 돌린다. 출력을 모두 돌려받으면
# Windows PowerShell 5.1은 네이티브 명령의 stderr 줄을 오류로 바꾼다. 따로 된 실행 공간(runspace)의 맨 위에서 돌려
# try로 감싸지 않는다: 감싸면 멈추지 않고 넘어갈 오류(명령을 찾지 못함 등)까지 catch가 받아 문제를 가린다
CAPTURED_BUILD = (
    "$shell = [PowerShell]::Create()\n"
    "[void]$shell.AddScript('cmd /c exit 0; & $env:HQ_BUILD *> $env:HQ_BUILD_OUT')\n"
    "$message = ''\n"
    "try { [void]$shell.Invoke() } catch { $e = $_.Exception; if ($e.InnerException) { $e = $e.InnerException };"
    " $message = $e.Message }\n"
    "[IO.File]::WriteAllText($env:HQ_ERROR, $message)\n"
)
UV_NOT_STARTED = "uv를 실행하지 못했습니다(설치되어 있지 않거나 PATH에 없거나 실행이 막혔습니다). 빌드하지 않았습니다."
ACL_REGION = re.compile(r"#region 쓰기 권한 검사\r?\n(.*?)#endregion", re.S)
SYSTEM, ADMINISTRATORS, USERS, AUTHENTICATED_USERS, EVERYONE = (
    "S-1-5-18", "S-1-5-32-544", "S-1-5-32-545", "S-1-5-11", "S-1-1-0")


def run_build(tmp_path: Path, prelude: str) -> tuple[str, str]:
    """prelude 뒤에 build.ps1을 돌린다. (마지막 오류 메시지 또는 '', build.ps1의 출력)."""
    error = tmp_path / "error.txt"
    out = tmp_path / "build-out.txt"
    env = {"HQ_BUILD": str(ROOT / "scripts" / "build.ps1"), "HQ_BUILD_OUT": str(out), "HQ_ERROR": str(error)}
    result = run_ps(prelude + CAPTURED_BUILD, env=env)
    assert result.returncode == 0, result.stderr
    return error.read_text(encoding="utf-8-sig"), out.read_text(encoding="utf-16")  # 5.1의 *>는 UTF-16으로 쓴다


@pytest.mark.parametrize(
    ("failing", "message", "calls"),
    [
        ("pytest", "테스트가 실패해서 빌드하지 않았습니다.", ["pytest"]),
        ("pyinstaller", "PyInstaller가 실패했습니다.", ["pytest", "pyinstaller"]),
    ],
    ids=["tests-fail", "pyinstaller-fails"],
)
def test_build_decides_by_exit_code_even_when_its_output_is_captured(tmp_path, failing, message, calls):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "uv.cmd").write_text(FAKE_UV, encoding="ascii", newline="")
    prelude = (
        f"$env:PATH = '{fake_bin};' + $env:PATH\n"
        f"$env:HQ_FAKE_CALLS = '{tmp_path / 'calls.txt'}'\n"
        f"$env:HQ_FAKE_FAIL = '{failing}'\n"
    )
    # stderr 줄에서 멈추지 않고 종료 코드로 실패를 알았다. 실패한 뒤의 단계는 부르지 않았다
    message_seen, output = run_build(tmp_path, prelude)
    assert message_seen == message
    assert "만든 파일" not in output
    assert (tmp_path / "calls.txt").read_text(encoding="ascii").split() == calls


def test_build_fails_when_uv_cannot_be_started(tmp_path):
    # uv를 띄우지 못하면 오류가 멈추지 않고 넘어가고 종료 코드도 남지 않는다. 앞 명령이 남긴 0으로 성공한 것처럼
    # 두 단계를 건너뛰고 예전 exe를 "만든 파일"로 알리면 안 된다
    prelude = "$env:PATH = $env:SystemRoot + '\\System32'\ncmd /c exit 0\n"
    message, output = run_build(tmp_path, prelude)
    assert message.startswith(UV_NOT_STARTED), message
    assert "만든 파일" not in output


@pytest.mark.parametrize("name", ["build.ps1", "make-shortcut.ps1"])
def test_packaging_scripts_parse_in_windows_powershell(name, tmp_path):
    assert parse_errors((ROOT / "scripts" / name).read_text(encoding="utf-8-sig"), tmp_path) == []


# ---------- make-shortcut.ps1의 쓰기 권한 검사 ----------


@pytest.fixture(scope="module")
def me() -> str:
    """이 계정의 SID."""
    out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, check=True).stdout
    return re.findall(r"S-1-5-[\d-]+", out.decode("ascii", errors="replace"))[-1]


def icacls(path: Path, *args: str) -> None:
    """tmp_path 아래의 시험용 폴더에만 쓴다."""
    subprocess.run(["icacls", str(path), *args], capture_output=True, check=True)


@pytest.fixture
def clean_dir(me):
    """나, SYSTEM, Administrators만 쓸 수 있는 폴더를 만든다. tmp_path는 TMPDIR 아래라 Everyone:(F)를 물려받으므로
    상속을 끊는다. 그 아래에 새로 만드는 폴더는 이 권한을 물려받는다."""

    def make(path: Path) -> Path:
        path.mkdir()
        icacls(path, "/inheritance:r", "/grant:r", *(f"*{sid}:(OI)(CI)F" for sid in (me, SYSTEM, ADMINISTRATORS)))
        return path

    return make


def acl_check(call: str, **paths: Path) -> str:
    """make-shortcut.ps1의 '쓰기 권한 검사' 부분만 떼어 call을 돌린다. 'PASS' 또는 'REFUSED: <메시지>'."""
    (functions,) = ACL_REGION.findall((ROOT / "scripts" / "make-shortcut.ps1").read_text(encoding="utf-8-sig"))
    script = functions + "\ntry { " + call + "; 'PASS' } catch { 'REFUSED: ' + $_.Exception.Message }\n"
    result = run_ps(script, env={f"HQ_{name.upper()}": str(path) for name, path in paths.items()})
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_a_folder_only_this_account_can_write_passes(tmp_path, clean_dir):
    root = clean_dir(tmp_path / "profile")
    folder = root / "Programs" / "hq"
    folder.mkdir(parents=True)
    assert acl_check("Assert-OnlyMineCanWrite $env:HQ_DIR", dir=folder) == "PASS"
    # 프로필 폴더까지만 본다. 그 위(여기서는 Everyone:(F)인 TMPDIR)는 보지 않는다
    assert acl_check("Assert-OnlyMineCanWriteUpTo $env:HQ_DIR $env:HQ_ROOT", dir=folder, root=root) == "PASS"


@pytest.mark.parametrize("sid", [USERS, AUTHENTICATED_USERS], ids=["Users", "Authenticated Users"])
def test_a_write_inherited_from_the_parent_is_refused(tmp_path, clean_dir, sid):
    parent = clean_dir(tmp_path / "parent")
    icacls(parent, "/grant", f"*{sid}:(OI)(CI)M")
    folder = parent / "hq"
    folder.mkdir()  # 부모의 항목을 물려받는다
    assert acl_check("Assert-OnlyMineCanWrite $env:HQ_DIR", dir=folder).startswith(f"REFUSED: {folder}에 ")


def test_an_inherit_only_generic_write_for_everyone_is_refused(tmp_path, clean_dir):
    # 폴더 자체에는 걸리지 않지만 그 안에 복사하는 exe가 물려받는다
    folder = clean_dir(tmp_path / "hq")
    icacls(folder, "/grant", f"*{EVERYONE}:(OI)(CI)(IO)(GW)")
    assert acl_check("Assert-OnlyMineCanWrite $env:HQ_DIR", dir=folder).startswith(f"REFUSED: {folder}에 ")


def test_a_deny_entry_alone_passes(tmp_path, clean_dir):
    # 나도 Everyone이므로 파일·폴더 만들기만 막는다(권한 읽기와 지우기는 막지 않는다)
    folder = clean_dir(tmp_path / "hq")
    icacls(folder, "/deny", f"*{EVERYONE}:(WD,AD)")
    assert acl_check("Assert-OnlyMineCanWrite $env:HQ_DIR", dir=folder) == "PASS"


@pytest.mark.parametrize("dirty", ["profile", "Programs"])
def test_a_dirty_ancestor_is_refused_even_when_the_folder_itself_is_clean(tmp_path, clean_dir, dirty):
    # 위 폴더에 쓸 수 있는 계정은 아래 폴더의 이름을 바꾸고 다른 폴더를 그 자리에 둘 수 있다
    root = clean_dir(tmp_path / "profile")
    folder = root / "Programs" / "hq"
    folder.mkdir(parents=True)
    ancestor = root if dirty == "profile" else root / "Programs"
    icacls(ancestor, "/grant", f"*{USERS}:F")  # 이 폴더에만(아래로 물려주지 않는다)
    assert acl_check("Assert-OnlyMineCanWrite $env:HQ_DIR", dir=folder) == "PASS"
    result = acl_check("Assert-OnlyMineCanWriteUpTo $env:HQ_DIR $env:HQ_ROOT", dir=folder, root=root)
    assert result.startswith(f"REFUSED: {ancestor}에 ")


def test_a_folder_outside_the_profile_is_refused(tmp_path, clean_dir):
    root = clean_dir(tmp_path / "profile")
    elsewhere = clean_dir(tmp_path / "elsewhere")
    result = acl_check("Assert-OnlyMineCanWriteUpTo $env:HQ_DIR $env:HQ_ROOT", dir=elsewhere, root=root)
    assert result.startswith("REFUSED: ") and "밖에" in result
