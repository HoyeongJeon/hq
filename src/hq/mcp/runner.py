"""스크립트 하나를 서버 하나에 실행한다. mcp-run.ps1을 돌리고, 어떻게 끝났는지를 종료 코드로 받아 결과 상태로 바꾼다.

템플릿이 세션을 열고 스크립트를 서버에서 작업으로 돌리며, 시간 한도와 취소 파일을 스스로 지킨다(스펙 8.2, 8.3).
프로세스에는 GRACE_SECONDS를 더 주고, 그래도 끝나지 않으면 psrun이 프로세스 트리를 강제로 끝낸다.
결과의 글(note)에는 비밀번호 파일 경로와 사용자 이름을 넣지 않는다.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from hq.mcp.psrun import run_ps_template
from hq.paths import resource
from hq.trustedhosts import is_trusted

GRACE_SECONDS = 10  # 템플릿이 시간 한도에서 작업을 멈추고 세션을 지우는 데 주는 시간(스펙의 강제 종료 전 정리 시간)
# mcp-run.ps1의 종료 코드. 10~14는 부트스트랩의 Get-HqExitCode와 같다. 여기 없는 코드는 UNKNOWN이다
STATUS_BY_CODE = {
    0: "OK",
    2: "SCRIPT_ERROR",
    10: "AUTH_FAILED",
    11: "UNREACHABLE",
    12: "NOT_TRUSTED",
    13: "QUOTA",
    14: "SECRET_UNREADABLE",
    20: "DISCONNECTED",
    21: "TIMEOUT",
    22: "CANCELLED",
}
_TIME_LIMIT = 21
_KILLED_NOTE = "강제로 끝냈습니다. 서버에서 계속 돌 수 있습니다."
_TIME_LIMIT_NOTE = "시간 한도에서 멈췄습니다. 그 전까지는 서버에서 실행됐습니다."


@dataclass(frozen=True)
class ServerJob:
    target: str  # 결과에 쓰는 대상 표기
    host: str
    username: str
    secret_path: Path
    script: str
    timeout_seconds: int
    cancel_path: Path  # 이 파일이 생기면 템플릿이 작업을 멈춘다


@dataclass(frozen=True)
class RunOutcome:
    target: str
    status: str
    output: str = ""
    errors: str = ""
    output_chars: int = 0  # 자르기 전 출력과 오류의 전체 글자 수
    truncated: bool = False
    seconds: float = 0.0
    note: str = ""
    exit_code: int | None = None  # 강제로 끝냈거나 띄우지 못했으면 None


def run_on_server(
    job: ServerJob, *, trusted: Sequence[str] | None, run=run_ps_template, template: Path | None = None
) -> RunOutcome:
    """trusted는 이 PC의 TrustedHosts 항목이다. 읽지 못했으면 None이고, 그때 12는 NOT_TRUSTED 그대로 둔다."""
    payload = {
        "host": job.host,
        "username": job.username,
        "secret_path": str(job.secret_path),
        "script": job.script,
        "timeout_seconds": job.timeout_seconds,
        "cancel_path": str(job.cancel_path),
    }
    started = time.monotonic()
    try:
        result = run(template or resource("ps/mcp-run.ps1"), payload, timeout=job.timeout_seconds + GRACE_SECONDS)
    except OSError:  # 예외 글자는 경로를 옮길 수 있어 결과에 넣지 않는다
        return RunOutcome(job.target, "SPAWN_FAILED", seconds=time.monotonic() - started)
    seconds = time.monotonic() - started
    if result.killed:
        status, note = "TIMEOUT", _KILLED_NOTE
    else:
        status = STATUS_BY_CODE.get(result.code, "UNKNOWN")
        if status == "NOT_TRUSTED" and trusted is not None and is_trusted(job.host, trusted):
            # 1312(0x8009030e)는 TrustedHosts에 없을 때뿐 아니라 계정 정보가 틀렸을 때도 난다(Bridge._on_exit와 같다)
            status = "AUTH_FAILED"
        note = _TIME_LIMIT_NOTE if result.code == _TIME_LIMIT else ""
    return RunOutcome(
        job.target,
        status,
        output=result.stdout,
        errors=result.stderr,
        output_chars=result.total_chars,
        truncated=result.total_chars > len(result.stdout) + len(result.stderr),
        seconds=seconds,
        note=note,
        exit_code=result.code,
    )
