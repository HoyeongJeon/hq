"""통합 테스트용: ConPTY 안에서 명령을 돌려 (출력, 종료 코드)를 돌려준다."""

import threading
import time

from hq.sessions import spawn_conpty

# ConPTY는 시작할 때 터미널 종류를 묻는다(DA1). 답이 없으면 약 3초 기다린 뒤에야 셸을 띄운다.
# 앱에서는 xterm.js가 답하고, 테스트에서는 xterm처럼 이 값으로 답한다.
DA1_QUERY = "\x1b[c"
DA1_ANSWER = "\x1b[?1;0c"


def run_pty(argv: list[str], *, writes=(), timeout: float = 30.0) -> tuple[str, int | None]:
    """writes는 (시작 후 몇 초 뒤, 보낼 글자) 목록. 프로세스가 끝날 때까지 기다린다."""
    proc = spawn_conpty(argv, 24, 80)  # 앱과 같은 어댑터. 소켓을 열지 않는다
    try:
        chunks: list[str] = []

        def reader() -> None:
            while True:
                try:
                    data = proc.read(4096)
                except EOFError:
                    return
                chunks.append(data)
                if DA1_QUERY in data:
                    proc.write(DA1_ANSWER)

        threading.Thread(target=reader, daemon=True).start()
        start = time.monotonic()
        for delay, text in writes:
            time.sleep(max(0.0, start + delay - time.monotonic()))
            if not proc.isalive():
                break  # 끝난 탭에 친 키는 아무 데도 가지 않는다(hq도 끝난 탭의 입력을 잠근다)
            proc.write(text)
        while proc.isalive() and time.monotonic() - start < timeout:
            time.sleep(0.05)
        if proc.isalive():
            proc.terminate(force=True)
            raise TimeoutError(f"{timeout}초 안에 끝나지 않았습니다: {''.join(chunks)[-500:]}")
        time.sleep(0.3)
        return "".join(chunks), proc.exitstatus
    finally:
        proc.close()  # 막혀 있는 읽기 스레드를 깨워 끝내고 의사 콘솔을 닫는다
