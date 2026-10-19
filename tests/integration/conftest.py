"""통합 테스트 공통 준비: 시험이 띄우는 셸(ConPTY, powershell.exe)이 사용자의 입력 기록에 줄을 남기지 않게 한다."""

from dataclasses import dataclass
from pathlib import Path

import pytest
from ps import run_ps


@dataclass(frozen=True)
class NormalShell:
    module_path: str  # 보통 자식 셸의 $env:PSModulePath
    psreadline: str  # 보통 자식 셸에서 Import-Module PSReadLine이 고르는 판


@pytest.fixture(scope="session")
def normal_shell(tmp_path_factory) -> NormalShell:
    """프로필을 바꾸기 전에 자식 셸이 평소에 보는 모듈 경로와 PSReadLine 판. 키를 치지 않으므로 기록은 남지 않는다."""
    out = tmp_path_factory.mktemp("normal-shell") / "shell.txt"
    script = (
        "Import-Module PSReadLine\n"
        "[IO.File]::WriteAllLines($env:HQ_OUT, [string[]]@($env:PSModulePath, (Get-Module PSReadLine).Version))\n"
    )
    result = run_ps(script, env={"HQ_OUT": str(out)})
    assert result.returncode == 0, result.stderr
    module_path, psreadline = out.read_text(encoding="utf-8-sig").splitlines()
    return NormalShell(module_path, psreadline)


@pytest.fixture(autouse=True)
def temp_appdata(tmp_path_factory, monkeypatch, normal_shell) -> Path:
    """시험마다 자식 셸에 임시 프로필을 준다(규칙 R16b-3). 돌려주는 값은 임시 APPDATA다.

    대화형 셸의 PSReadLine은 친 줄을 APPDATA 아래 Microsoft\\Windows\\PowerShell\\PSReadLine\\ConsoleHost_history.txt에
    쓴다. 자식 셸(spawn_conpty, run_ps)은 이 프로세스의 환경 변수를 물려받는다. 다만 PSReadLine은 그 위치를
    APPDATA 환경 변수가 아니라 [Environment]::GetFolderPath('ApplicationData')로 정하고, 이 값은 레지스트리의
    %USERPROFILE%\\AppData\\Roaming을 이 프로세스의 USERPROFILE로 풀어 만든다(이 PC에서 확인: APPDATA만 바꾸면
    기록은 그대로 사용자 폴더로 간다). 그래서 USERPROFILE을 바꾸고 APPDATA도 같은 곳으로 맞춘다.
    USERPROFILE을 바꾸면 문서 폴더도 따라 바뀌어, PSModulePath에 사용자 모듈 폴더가 없을 때(cmd에서 pytest를 띄운
    경우 등) 자식 셸이 그 폴더의 PSReadLine 대신 Windows에 든 2.0.0을 쓴다. 그래서 PSModulePath는 보통 자식 셸이
    보는 값으로 둔다.
    LOCALAPPDATA 환경 변수(hq 코드가 쓰는 것)는 바꾸지 않는다. 그래도 자식 셸의
    [Environment]::GetFolderPath('LocalApplicationData')는 USERPROFILE을 따라 임시 프로필의 AppData\\Local이 되고,
    그 폴더가 없으면 빈 값이 된다. 빈 값이면 Windows PowerShell이 ModuleAnalysisCache를 작업 폴더 아래 상대 경로
    (Microsoft\\Windows\\PowerShell\\...)에 써서 저장소에 폴더가 생긴다. 그래서 AppData\\Local도 만들어 둔다.
    """
    profile = tmp_path_factory.mktemp("profile")
    appdata = profile / "AppData" / "Roaming"
    appdata.mkdir(parents=True)
    (profile / "AppData" / "Local").mkdir()
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("PSModulePath", normal_shell.module_path)
    return appdata
