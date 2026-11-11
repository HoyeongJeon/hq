"""Claude가 서버에 보낸 스크립트의 실행 기록. 스펙 10장.

받은 폴더(보통 %LOCALAPPDATA%\\hq\\logs)의 mcp-audit-YYYY-MM.jsonl에 시작, 끝, 거절을 한 줄에 JSON 하나로 이어 쓰고
지우지 않는다. hq.log에 입출력을 남기지 않는 규칙의 예외다. 서버 출력, 비밀번호, 사용자 이름, 비밀번호 파일 경로는
남기지 않는다. 대상은 target 글자와 host만 남긴다.
"""

import hashlib
import json
import logging
import threading
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from hq.mcp.runner import RunOutcome

_log = logging.getLogger(__name__)


class AuditLog:
    def __init__(self, directory: Path, *, now: Callable[[], datetime] = datetime.now) -> None:
        self._directory = directory
        self._now = now
        # 동시에 돈 호출의 줄이 섞이거나 서로 덮어쓰지 않게 한 줄씩 쓴다
        self._lock = threading.Lock()

    def start(
        self, call_id: str, tool: str, targets: Sequence[tuple[str, str]], script: str, reason: str | None
    ) -> None:
        """실행 전에 부른다. targets는 (target, host)다. 쓰지 못하면 OSError를 그대로 던지고, 부르는 쪽은 실행하지
        않는다."""
        self._write(
            "start",
            {
                "call": call_id,
                "tool": tool,
                "targets": [{"target": target, "host": host} for target, host in targets],
                "script": script,
                # 짝 없는 서로게이트가 든 스크립트도 해시를 낸다(그런 글자가 없으면 보통의 UTF-8과 같다)
                "sha256": hashlib.sha256(script.encode("utf-8", "surrogatepass")).hexdigest(),
                "reason": reason,
            },
        )

    def end(self, call_id: str, outcomes: Sequence[RunOutcome], seconds: float) -> None:
        """실행이 끝난 뒤에 부른다. 쓰지 못해도 던지지 않고 경고만 남긴다. 이미 실행한 결과는 돌려줘야 한다."""
        results = [
            {
                "target": outcome.target,
                "status": outcome.status,
                "exit_code": outcome.exit_code,
                "seconds": outcome.seconds,
                "output_chars": outcome.output_chars,
            }
            for outcome in outcomes
        ]
        self._write_or_warn("end", {"call": call_id, "seconds": seconds, "results": results})

    def rejected(self, tool: str, script: str, why: str) -> None:
        """검사에서 거절한 스크립트. 쓰지 못해도 던지지 않고 경고만 남긴다. 거절 이유는 Claude에게 가야 한다."""
        self._write_or_warn("rejected", {"tool": tool, "script": script, "why": why})

    def _write_or_warn(self, event: str, fields: dict) -> None:
        try:
            self._write(event, fields)
        except OSError as exc:
            _log.warning("실행 기록에 %s 줄을 쓰지 못했습니다: %s", event, exc)

    def _write(self, event: str, fields: dict) -> None:
        moment = self._now()  # 시각과 파일 이름의 달이 같은 순간에서 온다
        record = {"event": event, "time": moment.isoformat(timespec="seconds"), **fields}
        line = json.dumps(record, ensure_ascii=False) + "\n"
        path = self._directory / f"mcp-audit-{moment:%Y-%m}.jsonl"
        with self._lock:
            self._directory.mkdir(parents=True, exist_ok=True)
            # 짝 없는 서로게이트는 \udXXX로 쓴다. JSON 문자열 안이므로 읽으면 같은 글자가 된다
            with path.open("a", encoding="utf-8", errors="backslashreplace", newline="\n") as file:
                file.write(line)
