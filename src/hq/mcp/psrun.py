"""MCP가 powershell.exe를 띄우는 유일한 곳. 템플릿 파일 하나를 돌려 값을 표준 입력으로 넘기고 출력과 오류를 받는다.

템플릿은 `[Console]::In.ReadToEnd() | ConvertFrom-Json`으로 값을 받고, `[Console]::Out.WriteLine`과
`[Console]::Error.WriteLine`으로만 쓰고, `[Environment]::Exit(n)`으로 끝낸다. 명령줄에는 템플릿을 읽어 점 실행하는
로더만 넣는다. 스크립트와 값을 명령줄에 넣으면 길이 한도에 걸리고 한글은 더 빨리 찬다(스펙 8.2).

로더는 템플릿의 PowerShell 스트림(출력, 오류, 경고, 자세한 정보, 디버그, 정보와 Write-Host)을 모두 버린다. 그 기록이
표준 오류에 가면 PowerShell이 CLIXML로 섞어 쓰는데, <Objs> 덩어리를 아무 데서나 끊어 써서 그 사이에 템플릿이 쓴 줄이
덩어리 안에 끼고, 기록의 글은 값을 옮길 수도 있다. 그래도 섞여 온 CLIXML($Host.UI.WriteErrorLine 등)은 읽으면서
지운다. 템플릿을 읽지 못하거나 템플릿이 예외로 끝나면 로더가 예외의 종류와 줄 번호만 한 줄로 쓰고 LOADER_FAILED로
끝낸다.
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

LOADER_FAILED = 70  # 템플릿을 읽지 못했거나 템플릿이 잡지 않은 예외로 끝났다
_CHUNK = 65536
# 프로세스가 끝난 뒤 남은 출력을 읽기까지 기다리는 시간. 그 프로세스가 띄운 다른 프로세스가 파이프를 쥐고 남아
# 있어도 이보다 오래 기다리지 않는다.
_DRAIN_SECONDS = 5.0
_KILL_SECONDS = 10  # taskkill과, 강제로 끝낸 프로세스를 기다리는 한도
_STOP_POLL_SECONDS = 0.2  # 멈춤 파일(stop_path)을 보는 간격
_TASKKILL = str(Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "taskkill.exe")
# 이 PC의 PowerShell 5.1(2026-11-11): 오류 스트림에 첫 기록이 오면 표시 줄을 곧바로 쓰고, 기록은 <Objs ...> 덩어리로
# 모아 두었다가 약 6,000자마다, 그리고 끝날 때 쓴다(</Objs> 뒤에 줄바꿈 없음).
_MARKER = "#< CLIXML\n"  # 줄바꿈을 \n 하나로 바꾼 뒤의 표시 줄
_OPEN = "<Objs"
_CLOSE = "</Objs>"


@dataclass(frozen=True)
class PsResult:
    code: int | None  # 강제로 끝냈으면 None
    stdout: str
    stderr: str
    total_chars: int  # 두 스트림에서 읽은 전체 글자 수(버린 것 포함, 지운 CLIXML은 빼고)
    killed: bool


def loader_argv(template: Path) -> list[str]:
    """템플릿을 UTF-8로 읽어 점 실행하는 로더. 템플릿에 BOM이 없어도 한글이 깨지지 않는다.

    읽기, 스크립트 블록 만들기, 실행에서 난 예외는 종류와 줄 번호만 쓴다. 예외 메시지는 값(사용자 이름, 비밀번호
    파일 경로)의 일부를 옮길 수 있다. 템플릿 안의 문장 종료 오류도 다음 문장으로 넘어가지 않고 여기서 끝난다.

    점 실행에 붙인 `*>$null`이 템플릿의 PowerShell 스트림을 모두 버린다. [Console]과 [Environment]::Exit는 그대로다.
    이 PC에서 확인한 부수 효과: 템플릿이 부른 외부 프로그램(cmd 등)의 출력도 PowerShell이 받아 버리고, 그 프로그램이
    표준 오류에 쓰면 $?가 False가 되며, $ErrorActionPreference가 Stop이면 그 줄이 예외가 되어 LOADER_FAILED로 끝난다.
    """
    loader = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
        "$ProgressPreference = 'SilentlyContinue'\n"
        f"$hqTemplate = {ps_json_literal(str(template))} | ConvertFrom-Json\n"
        "try {\n"
        "    . ([scriptblock]::Create([IO.File]::ReadAllText($hqTemplate, [Text.Encoding]::UTF8))) *>$null\n"
        "} catch {\n"
        "    $hqType = $_.Exception.GetType().FullName\n"
        "    $hqLine = $_.InvocationInfo.ScriptLineNumber\n"
        '    [Console]::Error.WriteLine("템플릿을 실행하지 못했습니다 ($hqType, 줄 $hqLine)")\n'
        f"    [Environment]::Exit({LOADER_FAILED})\n"
        "}\n"
    )
    encoded = base64.b64encode(loader.encode("utf-16-le")).decode("ascii")
    return [powershell_exe(), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


def run_ps_template(
    template: Path,
    payload: Mapping[str, object],
    *,
    timeout: float,
    keep_chars: int = 40_000,
    stop_path: Path | None = None,
    stop_grace: float = 10.0,
) -> PsResult:
    """템플릿을 돌리고 끝나기를 기다린다. timeout초가 지나면 프로세스 트리를 끝낸다. 띄우지 못하면 OSError를 던진다.

    stop_path가 있으면 기다리는 동안 약 0.2초마다 그 파일을 본다. 처음 보인 때부터 stop_grace초가 지나도 끝나지
    않으면 트리를 끝낸다. 템플릿이 스스로 정리할 시간이다. 원래의 timeout을 늘리지는 않는다.

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
    try:
        return _collect(proc, data, timeout, keep_chars, stop_path, stop_grace)
    except BaseException:
        _end_tree(proc)  # 무엇이 실패하든 띄운 프로세스 트리를 남기지 않고 예외를 그대로 올린다
        raise


def _collect(
    proc: subprocess.Popen[bytes],
    data: bytes,
    timeout: float,
    keep_chars: int,
    stop_path: Path | None,
    stop_grace: float,
) -> PsResult:
    kept = _Kept(keep_chars)
    readers = [
        threading.Thread(target=_drain, args=(proc.stdout, kept.stdout, kept), name="hq-ps-stdout", daemon=True),
        threading.Thread(
            target=_drain, args=(proc.stderr, kept.stderr, kept, _Clixml()), name="hq-ps-stderr", daemon=True
        ),
    ]
    for reader in readers:
        reader.start()
    # 값도 따로 쓴다. 템플릿이 읽지 않고 멈춰 있으면 쓰기가 막히는데, 그래도 timeout은 지켜야 한다
    threading.Thread(target=_feed, args=(proc.stdin, data), name="hq-ps-stdin", daemon=True).start()
    code = _wait(proc, timeout, stop_path, stop_grace)
    if code is None:
        _end_tree(proc)
    drained_by = time.monotonic() + _DRAIN_SECONDS
    for reader in readers:
        reader.join(max(0.0, drained_by - time.monotonic()))
    stdout, stderr, total_chars = kept.snapshot()
    return PsResult(code, stdout, stderr, total_chars, killed=code is None)


def _wait(proc: subprocess.Popen[bytes], timeout: float, stop_path: Path | None, stop_grace: float) -> int | None:
    """프로세스의 종료 코드. 한도까지 끝나지 않으면 None. 한도는 timeout이고, stop_path가 처음 보이면 그때부터
    stop_grace초로 줄어든다(늘어나지는 않는다)."""
    if stop_path is None:
        try:
            return proc.wait(timeout)
        except subprocess.TimeoutExpired:
            return None
    deadline = time.monotonic() + timeout
    seen = False
    while True:
        now = time.monotonic()
        if not seen and os.path.exists(stop_path):  # 보다 난 오류(권한 등)는 "없음"이다. Path.exists는 다시 던진다
            seen = True
            deadline = min(deadline, now + stop_grace)
        if now >= deadline:
            return None
        try:
            return proc.wait(min(_STOP_POLL_SECONDS, deadline - now))
        except subprocess.TimeoutExpired:
            pass


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


class _Clixml:
    """표준 오류에서 PowerShell이 쓴 CLIXML만 지운다: `#< CLIXML` 표시 줄과 `<Objs ...>...</Objs>` 덩어리.

    둘째 겹이다. 로더가 템플릿의 스트림을 모두 버리므로 보통은 올 일이 없고, $Host.UI.WriteErrorLine처럼 스트림을
    거치지 않는 글만 남는다.

    덩어리는 표시 줄이 온 뒤에만 찾는다. PowerShell은 덩어리보다 표시 줄을 먼저 쓰므로, 그 전의 `<Objs`는 템플릿이
    쓴 글이다. 조각 경계에 걸친 표시는 다음 조각까지 쥐고 있고(몇 글자뿐이다), 덩어리 안의 글자는 쥐지 않고 버린다.
    덩어리를 쓰는 사이에 템플릿이 쓴 줄은 덩어리 한가운데 끼어 글자만으로는 가를 수 없어 함께 지워진다.
    """

    def __init__(self) -> None:
        self._held = ""
        self._seen_marker = False
        self._in_block = False

    def feed(self, text: str, *, final: bool = False) -> str:
        """이어 온 글자에서 지금 내보내도 되는 글자를 돌려준다. final이면 쥐고 있던 글자도 정리한다."""
        text = self._held + text
        self._held = ""
        out = []
        i = 0
        while True:
            if self._in_block:
                end = text.find(_CLOSE, i)
                if end < 0:
                    if not final:
                        self._held = text[len(text) - _partial(text, i, _CLOSE) :]
                    return "".join(out)
                i = end + len(_CLOSE)
                self._in_block = False
                continue
            tokens = (_MARKER, _OPEN) if self._seen_marker else (_MARKER,)
            hits = [(at, token) for token in tokens if (at := text.find(token, i)) >= 0]
            if not hits:
                hold = 0 if final else max(_partial(text, i, token) for token in tokens)
                out.append(text[i : len(text) - hold])
                self._held = text[len(text) - hold :]
                return "".join(out)
            at, token = min(hits)
            out.append(text[i:at])
            i = at + len(token)
            if token == _MARKER:
                self._seen_marker = True
            else:
                self._in_block = True


def _partial(text: str, start: int, token: str) -> int:
    """text[start:]의 끝이 token의 앞부분(token 전체는 아니다)이면 그 길이. 다음 조각과 이어 봐야 token인지 안다."""
    for n in range(min(len(token) - 1, len(text) - start), 0, -1):
        if text.endswith(token[:n]):
            return n
    return 0


def _drain(stream: io.BufferedReader, parts: list[str], kept: _Kept, clixml: _Clixml | None = None) -> None:
    """스트림을 끝까지 읽는다. 조각 경계에서 갈라진 한글과 \\r\\n도 이어서 풀고, 줄바꿈은 \\n 하나로 바꿔 센다.

    clixml이 있으면 PowerShell의 CLIXML을 지운 뒤에 센다.
    """
    decoder = io.IncrementalNewlineDecoder(codecs.getincrementaldecoder("utf-8")(errors="replace"), translate=True)
    with stream:
        while chunk := stream.read1(_CHUNK):
            text = decoder.decode(chunk)
            kept.add(parts, clixml.feed(text) if clixml else text)
    text = decoder.decode(b"", final=True)
    kept.add(parts, clixml.feed(text, final=True) if clixml else text)


def _feed(stdin: io.BufferedWriter, data: bytes) -> None:
    """값을 쓰고 닫는다. 템플릿이 읽지 않고 끝났거나 강제로 끝났으면 파이프가 깨진다. 그 결과는 종료 코드로 안다."""
    with contextlib.suppress(OSError):
        stdin.write(data)
    with contextlib.suppress(OSError):
        stdin.close()


def _end_tree(proc: subprocess.Popen[bytes]) -> None:
    """자식과 그 아래 프로세스를 모두 끝낸다. taskkill의 표준 출력은 MCP 메시지 자리이므로 물려주지 않는다.

    taskkill이 실패하거나 뜨지 못하거나 멈춰도 직접 띄운 프로세스는 끝낸다. 어느 기다림도 _KILL_SECONDS를 넘지
    않는다. 그 사이에 OSError를 던지면 PowerShell을 띄우지 못한 것과 구별되지 않는다.
    """
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        subprocess.run(
            [_TASKKILL, "/PID", str(proc.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
            timeout=_KILL_SECONDS,
        )
    proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(_KILL_SECONDS)
