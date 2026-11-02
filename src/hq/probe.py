"""상태 점: 서버의 5985 포트가 TCP 연결을 받는지 병렬로 본다. 로그인은 하지 않는다."""

import asyncio
import contextlib
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

WINRM_PORT = 5985
# 이름이 여러 주소로 풀릴 때(localhost → ::1, 127.0.0.1) 앞 주소가 답하지 않으면 이만큼 뒤에 다음 주소도 시도한다.
_HAPPY_EYEBALLS_DELAY = 0.25


class _DaemonExecutor(ThreadPoolExecutor):
    """일마다 데몬 스레드를 하나씩 띄운다. 이름 풀이(getaddrinfo)를 여기서 돌린다.

    ThreadPoolExecutor의 스레드는 shutdown(wait=False) 뒤에도 프로세스가 끝날 때 기다리므로, 풀리지 않는 이름 하나가
    hq.exe를 OS의 이름 풀이 시간만큼 붙잡는다. 데몬 스레드는 기다리지 않는다. asyncio가 기본 executor로
    ThreadPoolExecutor만 받아서 그것을 물려받는다.
    """

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        future: Future[Any] = Future()

        def run() -> None:
            if not future.set_running_or_notify_cancel():
                return
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:
                future.set_exception(exc)
            else:
                future.set_result(result)

        threading.Thread(target=run, name="hq-probe-dns", daemon=True).start()
        return future

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        """기다릴 것도 거둘 것도 없다. 아직 풀고 있는 스레드는 그대로 두고 결과는 버린다."""


async def _one(host: str, port: int, timeout: float) -> bool:
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, happy_eyeballs_delay=_HAPPY_EYEBALLS_DELAY), timeout
        )
    except (OSError, TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return True


async def _all(hosts: list[str], port: int, timeout: float) -> dict[str, bool]:
    results = await asyncio.gather(*(_one(h, port, timeout) for h in hosts))
    return dict(zip(hosts, results, strict=True))


def probe_all(hosts: Iterable[str], port: int = WINRM_PORT, timeout: float = 1.0) -> dict[str, bool]:
    unique = list(dict.fromkeys(hosts))
    if not unique:
        return {}
    # 이름 풀이(getaddrinfo)는 executor 스레드에서 돈다. 이름마다 스레드를 하나씩 줘서
    # 느린 이름 뒤에 줄을 서지 않게 하고, 끝날 때 아직 풀고 있는 스레드를 기다리지 않는다
    # (asyncio.run은 기다린다). 스레드는 필요할 때만 생기므로 IP만 있으면 0개다.
    pool = _DaemonExecutor()
    loop = asyncio.new_event_loop()
    try:
        loop.set_default_executor(pool)
        return loop.run_until_complete(_all(unique, port, timeout))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        loop.close()
