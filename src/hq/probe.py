"""상태 점: 서버의 5985 포트가 TCP 연결을 받는지 병렬로 본다. 로그인은 하지 않는다."""

import asyncio
import contextlib
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor

WINRM_PORT = 5985
# 이름이 여러 주소로 풀릴 때(localhost → ::1, 127.0.0.1) 앞 주소가 답하지 않으면 이만큼 뒤에 다음 주소도 시도한다.
_HAPPY_EYEBALLS_DELAY = 0.25


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
    # 이름 풀이(getaddrinfo)는 executor 스레드에서 돈다. 서버마다 스레드를 하나씩 줘서
    # 느린 이름 뒤에 줄을 서지 않게 하고, 끝날 때 아직 풀고 있는 스레드를 기다리지 않는다
    # (asyncio.run은 기다린다). 스레드는 필요할 때만 생기므로 IP만 있으면 0개다.
    pool = ThreadPoolExecutor(max_workers=len(unique))
    loop = asyncio.new_event_loop()
    try:
        loop.set_default_executor(pool)
        return loop.run_until_complete(_all(unique, port, timeout))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        loop.close()
