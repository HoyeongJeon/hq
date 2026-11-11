"""통합 테스트용: 서버 대신 이 PC의 다른 powershell.exe에 원격 세션을 연다.

세션은 그 프로세스에 이름 있는 파이프로 연다(NamedPipeConnectionInfo, Enter-PSHostProcess가 쓰는 통로). WinRM을 쓰지
않고 이 PC 밖으로 나가지 않는다. 서버 역할 프로세스는 conftest.py의 local_server 픽스처가 띄운다.
"""

import os
import time


def pipe_session(variable: str = "$global:__hq_session") -> str:
    """$env:HQ_SERVER_PID 프로세스에 이름 있는 파이프로 세션을 열어 variable에 넣는 PowerShell 글자.

    이름 있는 파이프 런스페이스를 PSSession으로 감싼다(공개 생성자가 없다). Enter-PSSession -Session과
    Invoke-Command -Session이 받는 모양이다.
    """
    return (
        "$ci = New-Object System.Management.Automation.Runspaces.NamedPipeConnectionInfo([int]$env:HQ_SERVER_PID)\n"
        "$rs = [runspacefactory]::CreateRunspace($ci, $Host, "
        "[System.Management.Automation.Runspaces.TypeTable]::LoadDefaultTypeFiles())\n"
        "$rs.Open()\n"
        "$new = [System.Management.Automation.Runspaces.PSSession].GetConstructor("
        "[Reflection.BindingFlags]'Instance, NonPublic', $null, [type[]]@($rs.GetType()), $null)\n"
        f"{variable} = $new.Invoke(@($rs))\n"
    )


def wait_for_host_pipe(pid: int, timeout: float = 10.0) -> None:
    """PowerShell 프로세스는 시작하면 \\\\.\\pipe\\PSHost.<시각>.<pid>.DefaultAppDomain.<이름>을 연다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(f".{pid}.DefaultAppDomain." in name for name in os.listdir("\\\\.\\pipe\\")):
            return
        time.sleep(0.05)
    raise TimeoutError(f"서버 역할 PowerShell({pid})의 파이프가 열리지 않았습니다")
