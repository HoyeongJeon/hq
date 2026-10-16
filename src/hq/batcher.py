"""탭별 출력을 모아 두었다가 한 번에 화면으로 보낸다. 한 번에 보내는 양은 max_chars로 묶는다."""

import threading


class OutputBatcher:
    def __init__(self, max_chars: int = 200_000) -> None:
        self._max = max_chars
        self._pending: dict[str, list[str]] = {}
        self._size = 0
        self._lock = threading.Lock()

    def add(self, session_id: str, data: str) -> None:
        if not data:
            return
        with self._lock:
            self._pending.setdefault(session_id, []).append(data)
            self._size += len(data)

    def drain(self) -> dict[str, str]:
        with self._lock:
            out: dict[str, str] = {}
            budget = self._max
            for session_id in list(self._pending):
                if budget <= 0:
                    break
                parts = self._pending[session_id]
                taken: list[str] = []
                while parts and budget > 0:
                    chunk = parts[0]
                    if len(chunk) <= budget:
                        taken.append(parts.pop(0))
                        budget -= len(chunk)
                    else:
                        taken.append(chunk[:budget])
                        parts[0] = chunk[budget:]
                        budget = 0
                if taken:
                    out[session_id] = "".join(taken)
                if not parts:
                    del self._pending[session_id]
            self._size -= sum(len(v) for v in out.values())
            return out

    def discard(self, session_id: str) -> None:
        with self._lock:
            parts = self._pending.pop(session_id, [])
            self._size -= sum(len(p) for p in parts)

    def pending(self) -> int:
        with self._lock:
            return self._size
