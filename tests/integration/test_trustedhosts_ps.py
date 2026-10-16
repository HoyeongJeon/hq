import ctypes
import subprocess

import pytest
from ps import parse_errors, run_ps

from hq import trustedhosts
from hq.trustedhosts import AddResult, add_trusted_hosts, build_add_script, read_trusted_hosts

pytestmark = pytest.mark.integration

# 스크립트가 부르는 cmdlet을 같은 이름의 함수로 바꿔 둔다(PowerShell은 cmdlet보다 함수를 먼저 찾는다).
# 그래서 아래 시험은 이 PC의 WinRM 서비스와 TrustedHosts를 건드리지 않는다. 동작은 환경 변수로 정한다.
FAKES = r"""
function Get-Service { param($Name) [pscustomobject]@{ Name = $Name; Status = $env:HQ_STATUS } }
function Start-Service { param($Name) if ($env:HQ_FAIL -eq 'start') { throw 'start' }; Write-Output "start $Name" }
function Get-Item {
    param($Path)
    if ($env:HQ_FAIL -eq 'read') { throw 'read' }
    [pscustomobject]@{ Value = $env:HQ_CURRENT }
}
function Set-Item {
    param($Path, $Value, [switch]$Concatenate, [switch]$Force)
    if ($env:HQ_FAIL -eq 'write') { throw 'write' }
    Write-Output "set $Path $Value concatenate=$Concatenate force=$Force"
}
"""


def _run_with_fakes(script: str, *, current: str = "", status: str = "Stopped", fail: str = ""):
    return run_ps(FAKES + script, env={"HQ_CURRENT": current, "HQ_STATUS": status, "HQ_FAIL": fail})


def test_add_script_parses(tmp_path):
    assert parse_errors(build_add_script(["10.0.1.21"]), tmp_path) == []


def test_adds_only_the_missing_hosts_after_starting_a_stopped_service():
    result = _run_with_fakes(build_add_script(["10.0.1.21", "web01", "10.0.1.31"]), current="10.0.1.21, WEB01")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "start WinRM",
        r"set WSMan:\localhost\Client\TrustedHosts 10.0.1.31 concatenate=True force=True",
    ]


def test_running_service_and_nothing_missing_touch_nothing():
    result = _run_with_fakes(build_add_script(["10.0.1.21"]), current="10.0.1.21", status="Running")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == ""


def test_unreadable_host_list_exits_2_before_touching_the_service():
    script = trustedhosts.template_text().replace("__HQ_HOSTS__", "'not json'")
    result = _run_with_fakes(script)
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout.strip() == ""


@pytest.mark.parametrize(("fail", "code"), [("start", 3), ("read", 4), ("write", 5)])
def test_each_failing_step_exits_with_its_own_code(fail, code):
    result = _run_with_fakes(build_add_script(["10.0.1.31"]), current="10.0.1.21", fail=fail)
    assert result.returncode == code, result.stdout + result.stderr


def _is_admin() -> bool:
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


@pytest.mark.skipif(_is_admin(), reason="관리자 권한이면 이 PC의 WinRM 서비스와 TrustedHosts를 실제로 바꾼다")
def test_unelevated_launch_is_not_blocked_and_changes_nothing():
    # ShellExecuteExW에 넘길 것과 같은 명령줄을 관리자 권한 없이 실행한다. 백신(AhnLab V3)이 막으면
    # 프로세스를 만들 때 PermissionError(WinError 5)가 난다. 권한이 없어 3·4·5 중 하나로 끝나는 것은 정상이다.
    before = read_trusted_hosts()
    codes = []

    def run(file, parameters):
        result = subprocess.run(f'"{file}" {parameters}', capture_output=True, timeout=60, check=False)
        codes.append(result.returncode)
        return result.returncode

    assert add_trusted_hosts(["192.0.2.10"], run=run) is AddResult.FAILED
    assert codes[0] in (3, 4, 5), codes
    assert read_trusted_hosts() == before
