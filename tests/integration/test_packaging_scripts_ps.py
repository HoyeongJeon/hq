"""exe 묶기 스크립트(scripts/build.ps1, scripts/make-shortcut.ps1)를 Windows PowerShell 5.1로 확인한다.

build.ps1은 가짜 uv로 돌린다(진짜 테스트·PyInstaller는 돌리지 않는다). make-shortcut.ps1은 실제로 복사하고
바탕화면 바로가기를 바꾸므로 문법만 본다.
"""

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
# 이 셸 안에서 build.ps1의 출력을 모두 돌려받는다(*>). Windows PowerShell 5.1은 이때 네이티브 명령의 stderr 줄을
# 오류로 바꾼다
CAPTURED_BUILD = (
    "$env:PATH = $env:HQ_FAKE_BIN + ';' + $env:PATH\n"
    "try { & $env:HQ_BUILD *> $env:HQ_BUILD_OUT }\n"
    "catch { [IO.File]::WriteAllText($env:HQ_ERROR, $_.Exception.Message) }\n"
)


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
    error = tmp_path / "error.txt"
    env = {
        "HQ_FAKE_BIN": str(fake_bin),
        "HQ_FAKE_CALLS": str(tmp_path / "calls.txt"),
        "HQ_FAKE_FAIL": failing,
        "HQ_BUILD": str(ROOT / "scripts" / "build.ps1"),
        "HQ_BUILD_OUT": str(tmp_path / "build-out.txt"),
        "HQ_ERROR": str(error),
    }
    result = run_ps(CAPTURED_BUILD, env=env)
    assert result.returncode == 0, result.stderr
    # stderr 줄에서 멈추지 않고 종료 코드로 실패를 알았다. 실패한 뒤의 단계는 부르지 않았다
    assert error.read_text(encoding="utf-8") == message
    assert (tmp_path / "calls.txt").read_text(encoding="ascii").split() == calls


@pytest.mark.parametrize("name", ["build.ps1", "make-shortcut.ps1"])
def test_packaging_scripts_parse_in_windows_powershell(name, tmp_path):
    assert parse_errors((ROOT / "scripts" / name).read_text(encoding="utf-8-sig"), tmp_path) == []
