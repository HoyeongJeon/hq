"""통합 테스트용: ConPTY 안에서 명령을 돌려 (출력, 종료 코드)를 돌려준다."""

import threading
import time

from winpty import PtyProcess


def run_pty(argv: list[str], *, writes=(), timeout: float = 30.0) -> tuple[str, int | None]:
    """writes는 (시작 후 몇 초 뒤, 보낼 글자) 목록. 프로세스가 끝날 때까지 기다린다."""
    proc = PtyProcess.spawn(argv)
    chunks: list[str] = []

    def reader() -> None:
        while True:
            try:
                data = proc.read(4096)
            except EOFError:
                return
            chunks.append(data)
            if "\x1b[c" in data:
                proc.write("\x1b[?1;0c")  # ConPTY의 터미널 종류 질문에 xterm처럼 답한다

    threading.Thread(target=reader, daemon=True).start()
    start = time.monotonic()
    for delay, text in writes:
        time.sleep(max(0.0, start + delay - time.monotonic()))
        proc.write(text)
    while proc.isalive() and time.monotonic() - start < timeout:
        time.sleep(0.05)
    if proc.isalive():
        proc.terminate(force=True)
        raise TimeoutError(f"{timeout}초 안에 끝나지 않았습니다: {''.join(chunks)[-500:]}")
    time.sleep(0.3)
    return "".join(chunks), proc.exitstatus
