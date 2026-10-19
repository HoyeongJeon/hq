"""conftest.py의 프로필 격리 자체의 확인. 이것이 헛것이면 대화형 셸에 키를 치는 시험이 사용자의 입력 기록
(ConsoleHost_history.txt)에 줄을 남긴다. 사용자의 실제 기록 파일은 읽지도 건드리지도 않는다."""

import uuid

import pytest
from ps import run_ps
from ptyrun import run_pty

from hq.bootstrap import encoded_argv

pytestmark = pytest.mark.integration

HISTORY = ("Microsoft", "Windows", "PowerShell", "PSReadLine", "ConsoleHost_history.txt")


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def test_a_typed_command_lands_in_the_temporary_appdata_history(temp_appdata):
    history = temp_appdata.joinpath(*HISTORY)
    marker = f"hq-history-probe-{uuid.uuid4().hex}"
    # 안전장치: 이 셸의 기록이 임시 APPDATA로 가지 않으면 입력을 읽기 전에 끝낸다. 그러면 아래에서 친 글자는
    # 처리되지 않아 어떤 기록에도 들어가지 않는다.
    guard = (
        f"if ((Get-PSReadLineOption).HistorySavePath -ne {_ps_quote(str(history))}) {{\n"
        "    Write-Output ('history goes to ' + (Get-PSReadLineOption).HistorySavePath)\n"
        "    [Environment]::Exit(3)\n"
        "}\n"
    )
    output, code = run_pty(encoded_argv(guard), writes=[(1.5, f"Write-Output {marker}\r"), (2.5, "exit\r")])
    assert code == 0, output
    assert f"Write-Output {marker}" in history.read_text(encoding="utf-8").splitlines(), output


def test_child_shells_local_appdata_is_the_temporary_one(temp_appdata, tmp_path):
    # 이 값이 비면 Windows PowerShell은 ModuleAnalysisCache를 작업 폴더 아래 상대 경로(Microsoft\...)에 쓴다.
    out = tmp_path / "local.txt"
    script = "[IO.File]::WriteAllText($env:HQ_OUT, [Environment]::GetFolderPath('LocalApplicationData'))\n"
    result = run_ps(script, env={"HQ_OUT": str(out)})
    assert result.returncode == 0, result.stderr
    assert out.read_text(encoding="utf-8-sig") == str(temp_appdata.parent / "Local")


def test_child_shells_still_load_the_users_psreadline(normal_shell):
    # 프로필을 바꾸면 문서 폴더도 바뀐다. 그래도 자식 셸은 평소와 같은 PSReadLine(사용자 모듈 폴더의 것)을 쓴다.
    result = run_ps("Import-Module PSReadLine\nWrite-Output (Get-Module PSReadLine).Version.ToString()\n")
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [normal_shell.psreadline], result.stdout
