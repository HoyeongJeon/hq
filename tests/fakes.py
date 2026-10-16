"""단위 테스트용 가짜 ConPTY."""

import queue
import time


class FakePty:
    """feed()로 출력을 넣고 finish()로 끝낸다. 'exit\\r'을 받으면 스스로 끝난다(exit_on_exit=False면 버틴다)."""

    def __init__(self, *, exit_on_exit: bool = True) -> None:
        self._queue: queue.Queue[str | Exception | None] = queue.Queue()
        self._exit_on_exit = exit_on_exit
        self.written: list[str] = []
        self.alive = True
        self.exitstatus: int | None = None
        self.size: tuple[int, int] | None = None
        self.terminated = False
        self.closed = False

    def feed(self, text: str) -> None:
        self._queue.put(text)

    def fail(self, error: Exception) -> None:
        """다음 read()가 error를 던진다."""
        self._queue.put(error)

    def finish(self, code: int) -> None:
        if self.alive:
            self.exitstatus = code
            self.alive = False
            self._queue.put(None)

    def read(self, size: int = 1024) -> str:
        item = self._queue.get()
        if item is None:
            self._queue.put(None)
            raise EOFError("closed")
        if isinstance(item, Exception):
            raise item
        return item

    def write(self, text: str) -> int:
        if self.closed:
            raise EOFError("closed")  # ConPty.write처럼
        self.written.append(text)
        if text == "exit\r" and self._exit_on_exit:
            self.finish(0)
        return len(text)

    def isalive(self) -> bool:
        return self.alive

    def setwinsize(self, rows: int, cols: int) -> None:
        self.size = (rows, cols)

    def terminate(self, force: bool = False) -> bool:
        self.terminated = True
        self.finish(1)
        return True

    def close(self) -> None:
        """ConPty.close처럼: 살아 있던 프로세스는 함께 끝나고(종료 코드는 모르는 채), 읽기와 쓰기는 EOFError를 낸다."""
        self.closed = True
        self.alive = False
        self._queue.put(None)  # 막혀 있는 read()를 깨운다


class FakeSpawner:
    def __init__(self, *, exit_on_exit: bool = True) -> None:
        self._exit_on_exit = exit_on_exit
        self.calls: list[tuple[list[str], int, int]] = []
        self.ptys: list[FakePty] = []

    def __call__(self, argv: list[str], rows: int, cols: int) -> FakePty:
        pty = FakePty(exit_on_exit=self._exit_on_exit)
        self.calls.append((argv, rows, cols))
        self.ptys.append(pty)
        return pty


def wait_for(condition, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return bool(condition())
