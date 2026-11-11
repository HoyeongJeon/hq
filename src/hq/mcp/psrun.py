"""MCP가 powershell.exe를 띄우는 유일한 곳. 템플릿 파일 하나를 돌려 값을 표준 입력으로 넘기고 출력과 오류를 받는다.

템플릿은 `[Console]::In.ReadToEnd() | ConvertFrom-Json`으로 값을 받고, `[Console]::Out.WriteLine`과
`[Console]::Error.WriteLine`으로만 쓰고, `[Environment]::Exit(n)`으로 끝낸다. PowerShell의 오류 스트림에 맡기면
표준 오류에 `#< CLIXML`이 섞인다. 명령줄에는 템플릿을 읽어 점 실행하는 로더만 넣는다. 스크립트와 값을 명령줄에
넣으면 길이 한도에 걸리고 한글은 더 빨리 찬다(스펙 8.2).
"""

import base64
import codecs
import contextlib
import io
import json
import os
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from hq.bootstrap import powershell_exe, ps_json_literal

_CLIXML = "#< CLIXML"
_CHUNK = 65536
# 프로세스가 끝난 뒤 남은 출력을 읽기까지 기다리는 시간. 그 프로세스가 띄운 다른 프로세스가 파이프를 쥐고 남아
# 있어도 이보다 오래 기다리지 않는다.
_DRAIN_SECONDS = 5.0
_TASKKILL = str(Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "taskkill.exe")


@dataclass(frozen=True)
class PsResult:
    code: int | None  # 강제로 끝냈으면 None
    stdout: str
    stderr: str
    total_chars: int  # 두 스트림에서 읽은 전체 글자 수(버린 것 포함)
    killed: bool


def loader_argv(template: Path) -> list[str]:
    """템플릿을 UTF-8로 읽어 점 실행하는 로더. 템플릿에 BOM이 없어도 한글이 깨지지 않는다."""
    loader = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
        "$ProgressPreference = 'SilentlyContinue'\n"
        f"$hqTemplate = {ps_json_literal(str(template))} | ConvertFrom-Json\n"
        ". ([scriptblock]::Create([IO.File]::ReadAllText($hqTemplate, [Text.Encoding]::UTF8)))\n"
    )
    encoded = base64.b64encode(loader.encode("utf-16-le")).decode("ascii")
    return [powershell_exe(), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


def run_ps_template(
    template: Path, payload: Mapping[str, object], *, timeout: float, keep_chars: int = 40_000
) -> PsResult:
    """템플릿을 돌리고 끝나기를 기다린다. timeout초가 지나면 프로세스 트리를 끝낸다. 띄우지 못하면 OSError를 던진다.

    입력, 출력, 오류를 모두 따로 파이프로 이어 MCP의 표준 입출력을 물려주지 않는다. 출력과 오류는 합쳐 앞
    keep_chars자까지만 두고 나머지는 세기만 한다.
    """
    data = json.dumps(dict(payload), ensure_ascii=True).encode("ascii")
    proc = subprocess.Popen(
        loader_argv(template),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    kept = _Kept(keep_chars)
    readers = [
        threading.Thread(target=_drain, args=(proc.stdout, kept.stdout, kept), name="hq-ps-stdout", daemon=True),
        threading.Thread(target=_drain, args=(proc.stderr, kept.stderr, kept), name="hq-ps-stderr", daemon=True),
    ]
    for reader in readers:
        reader.start()
    # 값도 따로 쓴다. 템플릿이 읽지 않고 멈춰 있으면 쓰기가 막히는데, 그래도 timeout은 지켜야 한다
    threading.Thread(target=_feed, args=(proc.stdin, data), name="hq-ps-stdin", daemon=True).start()
    try:
        code = proc.wait(timeout)
    except subprocess.TimeoutExpired:
        _end_tree(proc)
        code = None
    drained_by = time.monotonic() + _DRAIN_SECONDS
    for reader in readers:
        reader.join(max(0.0, drained_by - time.monotonic()))
    stdout, stderr, total_chars = kept.snapshot()
    return PsResult(code, stdout, stderr.partition(_CLIXML)[0], total_chars, killed=code is None)


class _Kept:
    """두 스트림이 함께 쓰는 몫. 먼저 온 글자부터 합쳐 limit자까지만 두고 나머지는 세기만 한다."""

    def __init__(self, limit: int) -> None:
        self._lock = threading.Lock()
        self._room = limit
        self._total = 0
        self.stdout: list[str] = []
        self.stderr: list[str] = []

    def add(self, parts: list[str], text: str) -> None:
        with self._lock:
            self._total += len(text)
            piece = text[: self._room]
            if piece:
                self._room -= len(piece)
                parts.append(piece)

    def snapshot(self) -> tuple[str, str, int]:
        with self._lock:
            return "".join(self.stdout), "".join(self.stderr), self._total


def _drain(stream: io.BufferedReader, parts: list[str], kept: _Kept) -> None:
    """스트림을 끝까지 읽는다. 조각 경계에서 갈라진 한글과 \\r\\n도 이어서 풀고, 줄바꿈은 \\n 하나로 바꿔 센다."""
    decoder = io.IncrementalNewlineDecoder(codecs.getincrementaldecoder("utf-8")(errors="replace"), translate=True)
    with stream:
        while chunk := stream.read1(_CHUNK):
            kept.add(parts, decoder.decode(chunk))
    kept.add(parts, decoder.decode(b"", final=True))


def _feed(stdin: io.BufferedWriter, data: bytes) -> None:
    """값을 쓰고 닫는다. 템플릿이 읽지 않고 끝났거나 강제로 끝났으면 파이프가 깨진다. 그 결과는 종료 코드로 안다."""
    with contextlib.suppress(OSError):
        stdin.write(data)
    with contextlib.suppress(OSError):
        stdin.close()


def _end_tree(proc: subprocess.Popen[bytes]) -> None:
    """자식과 그 아래 프로세스를 모두 끝낸다. taskkill의 표준 출력은 MCP 메시지 자리이므로 물려주지 않는다.

    taskkill이 실패하거나 뜨지 못해도 직접 띄운 프로세스는 끝낸다. 그때 OSError를 던지면 PowerShell을 띄우지
    못한 것과 구별되지 않는다.
    """
    with contextlib.suppress(OSError):
        subprocess.run(
            [_TASKKILL, "/PID", str(proc.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
    proc.kill()
    proc.wait()
