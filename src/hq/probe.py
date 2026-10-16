"""상태 점: 서버의 5985 포트가 TCP 연결을 받는지 병렬로 본다. 로그인은 하지 않는다."""

import asyncio
import contextlib
from collections.abc import Iterable

WINRM_PORT = 5985


async def _one(host: str, port: int, timeout: float) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
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
    return asyncio.run(_all(unique, port, timeout))
