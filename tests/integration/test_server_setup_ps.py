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
# 바꾸는 명령의 실패(HQ_FAIL)는 실제 cmdlet처럼 종료되지 않는 오류(Write-Error)로 낸다.
FAKES = r"""
# 원격 세션 안에서 붙여넣은 경우: PowerShell은 원격 세션에서만 $PSSenderInfo를 둔다.
if ($env:HQ_REMOTE -eq '1') { $global:PSSenderInfo = [pscustomobject]@{ ConnectedUser = 'WEB01\opsadmin' } }
function Log([string]$line) { [IO.File]::AppendAllText($env:HQ_LOG, "$line`n", [Text.Encoding]::UTF8) }
function Write-Host { param([string]$Object) Log $Object }
function Get-Service { [CmdletBinding()] param($Name) [pscustomobject]@{ Name = $Name; Status = $env:HQ_STATUS } }
function Get-ChildItem {
    [CmdletBinding()] param($Path)
    if ($env:HQ_LISTENER -eq '1') { [pscustomobject]@{ Keys = @('Transport=HTTP', 'Address=*') } }
}
function Enable-PSRemoting {
    [CmdletBinding()] param([switch]$Force)
    Log 'CALL Enable-PSRemoting'
    if ($env:HQ_FAIL -eq 'enable') { Write-Error 'Access is denied.' }
}
function Get-Item {
    [CmdletBinding()] param($Path)
    if ($Path -like '*MaxMemoryPerShellMB') { [pscustomobject]@{ Value = $env:HQ_MEMORY } }
    else { [pscustomobject]@{ Value = $env:HQ_SHELLS } }
}
function Set-Item {
    [CmdletBinding()] param($Path, $Value, [switch]$Force)
    Log "CALL Set-Item $Path $Value"
    if ($env:HQ_FAIL -eq 'set') {
        Write-Error 'The config setting MaxShellsPerUser cannot be changed because is controlled by policies.'
    }
}
function Get-ItemProperty { [CmdletBinding()] param($Path, $Name) }
function New-ItemProperty {
    [CmdletBinding()] param($Path, $Name, $PropertyType, $Value, [switch]$Force)
    Log "CALL New-ItemProperty $Name=$Value"
    if ($env:HQ_FAIL -eq 'latfp') { Write-Error 'Requested registry access is not allowed.' }
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
# 스크립트는 서버 콘솔(RDP)에 붙여넣었다(원격 세션이 아니다).
READY = {"HQ_STATUS": "Running", "HQ_LISTENER": "1", "HQ_SHELLS": "2147483647", "HQ_MEMORY": "2147483647",
         "HQ_SID": "S-1-5-21-1-2-3-500", "HQ_FAIL": "", "HQ_REMOTE": ""}
RESTART = "CALL Restart-Service WinRM"
CHECK = "CALL Test-WSMan localhost"
REMOTE_NOTE = ("[HQ] 원격 세션 안에서 실행해서 WinRM을 다시 시작하지 않았습니다. 바뀐 한도는 새 세션부터 적용됩니다. "
               "적용되지 않으면 서버 콘솔(RDP)에서 Restart-Service WinRM 을 실행하세요.")
LISTENER = ({"HQ_STATUS": "Stopped", "HQ_LISTENER": "0"}, "CALL Enable-PSRemoting")
SHELLS = ({"HQ_SHELLS": "5"}, r"CALL Set-Item WSMan:\localhost\Shell\MaxShellsPerUser 30")
MEMORY = ({"HQ_MEMORY": "150"}, r"CALL Set-Item WSMan:\localhost\Shell\MaxMemoryPerShellMB 1024")


def _run(tmp_path, *, account="Administrator", allow_from=(), **server):
    log = tmp_path / "calls.log"
    script = FAKES + build_server_setup_script(account, allow_from)
    result = run_ps(script, env={**READY, **server, "HQ_LOG": str(log)})
    assert result.returncode == 0, result.stdout + result.stderr
    lines = log.read_text(encoding="utf-8-sig").splitlines()
    assert 'S="Error"' not in result.stderr, (lines, result.stderr)
    return lines


def _warning_index(lines):
    return next(i for i, line in enumerate(lines) if "[다시 연결]" in line)


@pytest.mark.parametrize(("server", "change"), [LISTENER, SHELLS, MEMORY], ids=["listener", "shells", "memory"])
def test_winrm_change_in_the_server_console_warns_then_restarts_winrm_before_the_self_check(tmp_path, server, change):
    lines = _run(tmp_path, **server)
    assert lines.count(RESTART) == 1, lines
    assert REMOTE_NOTE not in lines, lines
    assert lines.index(change) < _warning_index(lines) < lines.index(RESTART) < lines.index(CHECK), lines
    assert "(hq 탭 포함)" in lines[_warning_index(lines)]
    # 한 일 요약은 WinRM을 다시 시작하기 전에 보여 준다.
    assert lines.index("[HQ] 바꾼 것:") < _warning_index(lines), lines


@pytest.mark.parametrize(("server", "change"), [SHELLS, MEMORY], ids=["shells", "memory"])
def test_winrm_change_inside_a_remote_session_never_restarts_winrm(tmp_path, server, change):
    # 원격 세션 안에서 WinRM을 멈추면 시작하기 전에 이 세션이 끝나 WinRM이 멈춘 채로 남을 수 있다(규칙 R11-1).
    lines = _run(tmp_path, HQ_REMOTE="1", **server)
    assert RESTART not in lines, lines
    assert not any("[다시 연결]" in line for line in lines), lines
    assert lines.index(change) < lines.index("[HQ] 바꾼 것:") < lines.index(REMOTE_NOTE) < lines.index(CHECK), lines


def test_remote_session_never_runs_enable_psremoting(tmp_path):
    # Enable-PSRemoting은 세션 구성을 다시 등록하면서 WinRM을 스스로 다시 시작할 수 있다(규칙 R11-2).
    # 원격 세션으로 들어왔다면 WinRM은 이미 켜져 있으니, 리스너 확인이 실패해도 부르지 않는다.
    lines = _run(tmp_path, HQ_REMOTE="1", HQ_LISTENER="0")
    assert "CALL Enable-PSRemoting" not in lines, lines
    assert RESTART not in lines, lines
    assert "[HQ] 바꾼 것 없음. 이미 설정되어 있습니다." in lines, lines


def test_remote_session_without_a_winrm_change_prints_no_note(tmp_path):
    lines = _run(tmp_path, HQ_REMOTE="1")
    assert RESTART not in lines, lines
    assert REMOTE_NOTE not in lines, lines
    assert "[HQ] 바꾼 것 없음. 이미 설정되어 있습니다." in lines, lines
    assert CHECK in lines, lines


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


# 바꾸는 명령이 실패하면(그룹 정책이 값을 고정했거나 백신이 레지스트리 쓰기를 막는 등) "바꾼 것"에 넣지 않고,
# 그 때문에 WinRM을 다시 시작하지도 않는다(규칙 R11-2).
@pytest.mark.parametrize(
    ("server", "failure"),
    [({"HQ_SHELLS": "5"}, "[HQ] 계정당 동시 세션 한도를 바꾸지 못했습니다: "),
     ({"HQ_MEMORY": "150"}, "[HQ] 셸당 메모리 한도를 바꾸지 못했습니다: ")],
    ids=["shells", "memory"],
)
def test_limit_blocked_by_policy_is_reported_not_counted_and_does_not_restart(tmp_path, server, failure):
    lines = _run(tmp_path, HQ_FAIL="set", **server)
    assert any(line.startswith(failure) and "controlled by policies" in line for line in lines), lines
    assert RESTART not in lines, lines
    assert not any("[다시 연결]" in line for line in lines), lines
    assert "[HQ] 바꾼 것 없음. 이미 설정되어 있습니다." in lines, lines
    assert CHECK in lines, lines


def test_failed_token_filter_policy_write_gives_no_warning(tmp_path):
    lines = _run(tmp_path, account="opsadmin", HQ_SID="S-1-5-21-1-2-3-1001", HQ_FAIL="latfp")
    assert "CALL New-ItemProperty LocalAccountTokenFilterPolicy=1" in lines, lines
    assert any(line.startswith("[HQ] LocalAccountTokenFilterPolicy를 바꾸지 못했습니다: ") for line in lines), lines
    assert not any(line.startswith("[HQ] 주의:") for line in lines), lines
    assert "[HQ] 바꾼 것 없음. 이미 설정되어 있습니다." in lines, lines


def test_failed_enable_psremoting_stops_before_anything_else(tmp_path):
    lines = _run(tmp_path, HQ_FAIL="enable", **LISTENER[0])
    assert any(line.startswith("[HQ] WinRM을 켜지 못했습니다: ") for line in lines), lines
    assert RESTART not in lines, lines
    assert "[HQ] 바꾼 것:" not in lines, lines
    assert not any(line.startswith("CALL Set-Item") for line in lines), lines
