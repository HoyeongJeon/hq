import pytest
from ps import parse_errors, run_ps

from hq.server_setup import build_server_setup_script

pytestmark = pytest.mark.integration


def test_setup_script_parses(tmp_path):
    assert parse_errors(build_server_setup_script("opsadmin", ["10.0.0.50"]), tmp_path) == []


# 스크립트가 부르는 cmdlet을 같은 이름의 함수로 바꿔 둔다(PowerShell은 cmdlet보다 함수를 먼저 찾는다).
# 그래서 아래 시험은 이 PC의 WinRM, 레지스트리, 방화벽을 건드리지 않는다. 서버 상태는 환경 변수로 정한다.
# 스크립트의 출력(Write-Host)과 바꾸는 명령의 호출을 같은 UTF-8 파일에 차례대로 적는다.
# (Write-Host를 표준 출력으로 받으면 한글이 깨지고, Out-Null로 버리는 호출은 보이지 않는다.)
FAKES = r"""
function Log([string]$line) { [IO.File]::AppendAllText($env:HQ_LOG, "$line`n", [Text.Encoding]::UTF8) }
function Write-Host { param([string]$Object) Log $Object }
function Get-Service { [CmdletBinding()] param($Name) [pscustomobject]@{ Name = $Name; Status = $env:HQ_STATUS } }
function Get-ChildItem {
    [CmdletBinding()] param($Path)
    if ($env:HQ_LISTENER -eq '1') { [pscustomobject]@{ Keys = @('Transport=HTTP', 'Address=*') } }
}
function Enable-PSRemoting { [CmdletBinding()] param([switch]$Force) Log 'CALL Enable-PSRemoting' }
function Get-Item {
    [CmdletBinding()] param($Path)
    if ($Path -like '*MaxMemoryPerShellMB') { [pscustomobject]@{ Value = $env:HQ_MEMORY } }
    else { [pscustomobject]@{ Value = $env:HQ_SHELLS } }
}
function Set-Item { [CmdletBinding()] param($Path, $Value, [switch]$Force) Log "CALL Set-Item $Path $Value" }
function Get-ItemProperty { [CmdletBinding()] param($Path, $Name) }
function New-ItemProperty {
    [CmdletBinding()] param($Path, $Name, $PropertyType, $Value, [switch]$Force)
    Log "CALL New-ItemProperty $Name=$Value"
}
function New-Object {
    [CmdletBinding()] param($TypeName, $ArgumentList, $ComObject)
    $fake = [pscustomobject]@{ Fake = $true }
    if ($TypeName -eq 'Security.Principal.WindowsPrincipal') {
        return $fake | Add-Member -PassThru -MemberType ScriptMethod -Name IsInRole -Value { $true }
    }
    if ($TypeName -eq 'Security.Principal.NTAccount') {
        $translate = { [pscustomobject]@{ Value = $env:HQ_SID } }
        return $fake | Add-Member -PassThru -MemberType ScriptMethod -Name Translate -Value $translate
    }
    if ($ComObject -eq 'HNetCfg.FwPolicy2') {
        $rule = [pscustomobject]@{
            Name = 'WINRM-HTTP-In-TCP'; Direction = 1; Protocol = 6; LocalPorts = '5985'; Action = 1; Enabled = $true
        }
        $setter = { Log "CALL set RemoteAddresses $($args[0])" }
        $rule | Add-Member -MemberType ScriptProperty -Name RemoteAddresses -Value { '*' } -SecondValue $setter
        return [pscustomobject]@{ Rules = @($rule) }
    }
    throw "시험에 없는 New-Object: $TypeName $ComObject"
}
function Restart-Service {
    [CmdletBinding()] param($Name, [switch]$Force)
    Log "CALL Restart-Service $Name"
    if ($env:HQ_FAIL -eq 'restart') { throw 'restart failed' }
}
function Test-WSMan { [CmdletBinding()] param($ComputerName) Log "CALL Test-WSMan $ComputerName" }
"""

# 이미 다 설정된 서버: WinRM이 돌고 5985 리스너가 있고, 한도가 충분하고, 계정은 내장 Administrator다.
READY = {"HQ_STATUS": "Running", "HQ_LISTENER": "1", "HQ_SHELLS": "2147483647", "HQ_MEMORY": "2147483647",
         "HQ_SID": "S-1-5-21-1-2-3-500", "HQ_FAIL": ""}
RESTART = "CALL Restart-Service WinRM"
CHECK = "CALL Test-WSMan localhost"


def _run(tmp_path, *, account="Administrator", allow_from=(), **server):
    log = tmp_path / "calls.log"
    script = FAKES + build_server_setup_script(account, allow_from)
    result = run_ps(script, env={**READY, **server, "HQ_LOG": str(log)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'S="Error"' not in result.stderr, result.stderr
    return log.read_text(encoding="utf-8-sig").splitlines()


def _warning_index(lines):
    return next(i for i, line in enumerate(lines) if "[다시 연결]" in line)


@pytest.mark.parametrize(
    ("server", "change"),
    [
        ({"HQ_STATUS": "Stopped", "HQ_LISTENER": "0"}, "CALL Enable-PSRemoting"),
        ({"HQ_SHELLS": "5"}, r"CALL Set-Item WSMan:\localhost\Shell\MaxShellsPerUser 30"),
        ({"HQ_MEMORY": "150"}, r"CALL Set-Item WSMan:\localhost\Shell\MaxMemoryPerShellMB 1024"),
    ],
    ids=["listener", "shells", "memory"],
)
def test_winrm_change_warns_then_restarts_winrm_before_the_self_check(tmp_path, server, change):
    lines = _run(tmp_path, **server)
    assert lines.count(RESTART) == 1, lines
    assert lines.index(change) < _warning_index(lines) < lines.index(RESTART) < lines.index(CHECK), lines
    assert "hq 탭" in lines[_warning_index(lines)]
    # 한 일 요약은 WinRM을 다시 시작하기 전에 보여 준다. 다시 시작하면 hq 탭은 그 뒤를 받지 못한다.
    assert lines.index("[HQ] 바꾼 것:") < _warning_index(lines), lines


@pytest.mark.parametrize(
    ("options", "change"),
    [
        ({}, None),
        ({"account": "opsadmin", "HQ_SID": "S-1-5-21-1-2-3-1001"},
         "CALL New-ItemProperty LocalAccountTokenFilterPolicy=1"),
        ({"allow_from": ["10.0.0.50", "10.0.0.51"]}, "CALL set RemoteAddresses 10.0.0.50,10.0.0.51"),
    ],
    ids=["nothing", "token-filter-policy", "firewall-scope"],
)
def test_no_winrm_change_means_no_restart_and_no_warning(tmp_path, options, change):
    lines = _run(tmp_path, **options)
    assert RESTART not in lines, lines
    assert not any("[다시 연결]" in line for line in lines), lines
    assert CHECK in lines, lines
    if change is None:
        assert "[HQ] 바꾼 것 없음. 이미 설정되어 있습니다." in lines, lines
        assert not any(line.startswith("CALL ") and line != CHECK for line in lines), lines
    else:
        assert change in lines, lines


def test_failed_restart_is_reported_and_the_self_check_still_runs(tmp_path):
    lines = _run(tmp_path, HQ_SHELLS="5", HQ_FAIL="restart")
    assert any(line.startswith("[HQ] WinRM을 다시 시작하지 못했습니다") for line in lines), lines
    assert lines.index(RESTART) < lines.index(CHECK), lines
