"""서버 탭마다 powershell.exe 하나를 ConPTY 안에서 띄우고 끝날 때까지 관리한다.

세션마다 스레드 두 개를 둔다. 읽기 스레드는 출력을 계속 읽어 on_output으로 넘긴다(안 읽으면
자식 프로세스가 멈춘다). 감시 스레드는 isalive()로 끝남을 알아채 on_exit에 종료 코드를 넘긴다.
ConPTY는 프로세스가 끝난 뒤 EOF를 약 5초 늦게 내므로 EOF를 기다리지 않는다(2026-10-16 저수준
winpty.PTY로 다시 재도 같았다).

준비 신호(spec 7.3): ready_marker를 주면 프로세스마다 출력에서 그 신호를 볼 때까지 탭의 입력을 보내지 않고
쥐고 있다가, 보면 친 순서대로 보낸다. 부트스트랩은 안전장치를 건 직후에 신호를 내므로, 안전장치가 걸리지 않은
탭(백신이 본문을 막아 내 PC의 프롬프트가 뜬 탭)에는 입력이 가지 않는다. ready_timeout초 안에 신호가 오지 않으면
프로세스를 끝내고 NOT_READY_CODE로 보고한다.

신호는 콘솔 제목을 ready_marker로 바꾸면 ConPTY가 내보내는 제목 시퀀스 하나(ESC ] 0 또는 2 ; 글자, BEL 또는 ESC \\)다.
글자만 보지 않는다. 제한 언어 모드에서는 신호도 안전장치도 없는 채 접속될 수 있는데, 별명에 그 글자가 들어 있으면
원격 프롬프트가 그것을 그냥 글자로 찍어 문이 열릴 수 있기 때문이다.
"""

import contextlib
import ctypes
import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from ctypes import wintypes
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from winpty import PTY, Backend, WinptyError

from hq.bootstrap import NOT_READY_CODE
from hq.validation import is_hex_id

log = logging.getLogger(__name__)

HELD_INPUT_LIMIT = 64 * 1024  # 준비 신호 전에 쥐고 있을 입력(글자 수). 넘게 하는 입력부터는 버린다

# 터미널(xterm.js)이 ConPTY의 질의에 답하는 응답: DA1, DA2, 커서 위치, 장치 상태, 포커스 들어옴/나감.
# ConPTY는 시작할 때 DA1 답을 기다리므로(답이 없으면 약 3초) 이것까지 쥐고 있으면 신호가 늦게 오거나 오지 않는다.
# Enter가 없어 명령을 실행할 수 없으므로 신호 전에도 보낸다. 다른 글자가 섞인 입력은 통째로 쥐고 있는다.
_TERMINAL_REPLIES = re.compile(r"(?:\x1b\[\?[0-9;]*c|\x1b\[>[0-9;]*c|\x1b\[[0-9]+;[0-9]+R|\x1b\[0n|\x1b\[[IO])+")
_TITLE_START = ("\x1b]0;", "\x1b]2;")  # OSC 0(아이콘 이름과 제목), OSC 2(제목)
_TITLE_END = ("\x07", "\x1b\\")  # BEL, ST


def _title_signal(marker: str) -> tuple[re.Pattern[str], int]:
    """제목을 marker로 바꾸는 시퀀스를 찾는 정규식과 그 시퀀스의 가장 긴 길이."""
    alternatives = "|".join(re.escape(part) for part in _TITLE_START)
    endings = "|".join(re.escape(part) for part in _TITLE_END)
    pattern = re.compile(f"(?:{alternatives}){re.escape(marker)}(?:{endings})")
    return pattern, max(map(len, _TITLE_START)) + len(marker) + max(map(len, _TITLE_END))

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.SetConsoleCtrlHandler.argtypes = [ctypes.c_void_p, wintypes.BOOL]
_kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL


class PtyLike(Protocol):
    @property
    def exitstatus(self) -> int | None: ...

    def read(self, size: int = ...) -> str: ...
    def write(self, s: str) -> object: ...
    def isalive(self) -> bool: ...
    def setwinsize(self, rows: int, cols: int) -> None: ...
    def terminate(self, force: bool = False) -> object: ...
    def close(self) -> None: ...


Spawner = Callable[[list[str], int, int], PtyLike]


class ConPty:
    """winpty.PTY(ConPTY)를 PtyLike로 감싼다.

    winpty.PtyProcess는 출력을 넘기려고 프로세스마다 127.0.0.1에 TCP 포트를 열어 두므로 쓰지 않는다
    (hq는 수신 포트를 하나도 열지 않는다). 읽기는 한 스레드에서만 한다.
    """

    def __init__(self, pty: PTY) -> None:
        self._pty: PTY | None = pty
        self.pid: int = pty.pid
        self._exitstatus: int | None = None
        self._lock = threading.Lock()
        self._reading = False

    @property
    def exitstatus(self) -> int | None:
        pty = self._pty
        return self._exitstatus if pty is None else pty.get_exitstatus()

    def read(self, size: int = 4096) -> str:
        """출력이 올 때까지 기다렸다가 와 있는 만큼 돌려준다(size는 쓰지 않는다). 끝나거나 닫히면 EOFError."""
        with self._lock:
            pty = self._pty
            if pty is None:
                raise EOFError("ConPTY가 닫혔습니다.")
            self._reading = True
        try:
            return pty.read(blocking=True)
        except WinptyError as exc:
            if self._pty is not None and pty.isalive():
                raise  # 열려 있고 프로세스도 살아 있다: EOF가 아닌 진짜 오류. 읽기 스레드가 warning으로 남긴다
            raise EOFError(str(exc)) from exc  # 프로세스가 끝난 뒤의 늦은 EOF, 또는 close()의 cancel_io
        finally:
            self._reading = False

    def write(self, s: str) -> int:
        pty = self._pty
        if pty is None:
            raise EOFError("ConPTY가 닫혔습니다.")
        return pty.write(s)

    def isalive(self) -> bool:
        pty = self._pty
        return pty is not None and pty.isalive()

    def setwinsize(self, rows: int, cols: int) -> None:
        pty = self._pty
        if pty is not None:
            pty.set_size(cols, rows)

    def terminate(self, force: bool = False) -> bool:
        """TerminateProcess로 끝낸다. Windows에는 부드럽게 끝내라는 신호가 없어 force와 상관없이 같다."""
        if not self.isalive():
            return True
        try:
            os.kill(self.pid, signal.SIGTERM)  # Windows에서는 TerminateProcess
        except OSError:
            if self.isalive():
                raise
        deadline = time.monotonic() + 1.0
        while self.isalive() and time.monotonic() < deadline:
            time.sleep(0.01)
        return not self.isalive()

    def close(self) -> None:
        """PTY를 놓는다. 여러 번 불러도 된다.

        막혀 있는 읽기를 cancel_io로 깨워 끝낸다. 마지막 참조가 사라지면 의사 콘솔이 닫히고, 그 안에
        아직 남은 프로세스도 함께 끝난다.
        """
        with self._lock:
            pty = self._pty
            if pty is None:
                return
            self._exitstatus = pty.get_exitstatus()
            self._pty = None
        deadline = time.monotonic() + 1.0
        while self._reading and time.monotonic() < deadline:
            # 걸려 있는 읽기가 아직 없으면 WinptyError가 난다. 읽기가 막 시작하는 참이면 다음 바퀴에 취소한다
            with contextlib.suppress(WinptyError):
                pty.cancel_io()
            time.sleep(0.01)
        if self._reading:
            log.warning("ConPTY 읽기를 멈추지 못함 pid=%s", self.pid)


def _let_children_handle_ctrl_c() -> None:
    """Ctrl+C를 무시하는 상태는 자식 프로세스에 물려진다.

    hq가 그 상태로 시작됐으면(예: CREATE_NEW_PROCESS_GROUP으로 띄움) 탭에 보낸 Ctrl+C(\\x03)가 실행 중인
    명령을 멈추지 못한다. 그래서 띄우기 전에 무시 상태를 푼다. hq 자신의 Ctrl+C 처리도 기본값으로
    돌아가지만, 창 프로그램인 hq에는 콘솔이 없어 달라지는 것이 없다.
    """
    if not _kernel32.SetConsoleCtrlHandler(None, False):
        log.warning("Ctrl+C 처리를 켜지 못함 (오류 %s)", ctypes.get_last_error())


def spawn_conpty(argv: list[str], rows: int, cols: int) -> ConPty:
    """argv를 ConPTY 안에서 띄운다. 작업 폴더와 환경 변수는 hq의 것을 물려준다.

    띄우지 못하면 OSError. winpty는 CreateProcess 실패(예: 백신이 막은 액세스 거부)를 WinptyError로 던지므로
    메시지를 그대로 옮겨 OSError로 바꾼다.
    """
    app = shutil.which(argv[0])
    if app is None:
        raise FileNotFoundError(f"실행 파일을 찾을 수 없습니다: {argv[0]}")
    _let_children_handle_ctrl_c()
    try:
        pty = PTY(cols, rows, backend=Backend.ConPTY)
        # winpty.PtyProcess.spawn과 같은 모양으로 넘긴다(winpty가 앞에 실행 파일 경로를 붙인다)
        pty.spawn(app, cmdline=" " + subprocess.list2cmdline(argv[1:]) if len(argv) > 1 else None)
    except WinptyError as exc:
        raise OSError(str(exc)) from exc
    return ConPty(pty)


class SessionError(Exception):
    pass


class _Gate(Enum):
    HOLD = "hold"  # 준비 신호를 기다린다. 입력은 쥐고 있고 터미널 응답만 보낸다
    OPEN = "open"  # 입력을 그대로 보낸다
    SHUT = "shut"  # 신호 없이 시간이 지나 프로세스를 끝내는 중이다. 입력은 버린다


@dataclass
class _Held:
    """준비 신호 전에 친 입력. 친 순서대로 온전한 입력만 모은다.

    HELD_INPUT_LIMIT에 들어가지 않는 입력이 오면 그것과 그 뒤의 입력을 모두 버린다. 입력을 잘라 반쪽을 남기면
    붙여넣은 줄의 앞부분이 원격 프롬프트에 남아 다음 Enter에 실행될 수 있기 때문이다.
    """

    chunks: list[str] = field(default_factory=list)
    size: int = 0
    overflowed: bool = False

    def add(self, data: str) -> bool:
        """data를 모은다. 이번에 처음으로 넘쳐 입력을 버렸으면 True."""
        if self.overflowed:
            return False
        if self.size + len(data) > HELD_INPUT_LIMIT:
            self.overflowed = True
            return True
        self.chunks.append(data)
        self.size += len(data)
        return False


@dataclass(eq=False)
class _Session:
    id: str
    server_id: str
    pty: PtyLike
    size: tuple[int, int]  # (rows, cols). 다시 연결이 새 프로세스를 이 크기로 띄운다
    gate: _Gate = _Gate.OPEN
    held: _Held = field(default_factory=_Held)
    generation: int = 0
    running: bool = True
    closing: bool = False
    # pty를 쓰는 일(입력, 크기 바꾸기, 쥐고 있던 입력 보내기, 다시 연결의 교체, 닫기)을 한 줄로 세우고
    # gate, held, size를 지킨다. 이 잠금 안에서 on_output/on_exit를 부르지 않는다(콜백이 write를 부를 수 있다).
    # 잠금 순서: 관리자 잠금(_lock)을 쥔 채 이 잠금을 잡는 곳은 reconnect의 시작뿐이고, 반대로는 잡지 않는다.
    # 이 잠금을 쥔 채 pty.write를 하므로 쓰기가 막히면 이 탭의 호출이 멈춘다. reconnect가 그 탭의 잠금을
    # 기다리는 동안에는 관리자 잠금도 쥐고 있으므로 모든 탭의 호출(_get을 거치는 write, resize 등)까지 멈춘다.
    # reconnect는 프로세스가 끝난 뒤에만 하고 끝난 ConPTY에 쓰면 곧바로 돌아오므로(측정) 실제로는 막히지 않는다고 본다.
    # 감시 스레드는 그래서 관리자 잠금을 놓은 뒤에 이 잠금을 잡는다.
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class SessionManager:
    def __init__(
        self,
        *,
        spawn: Spawner,
        on_output: Callable[[str, str], None],
        on_exit: Callable[[str, int | None], None],
        poll_interval: float = 0.2,
        drain_delay: float = 0.3,
        close_timeout: float = 2.0,
        ready_marker: str | None = None,
        ready_timeout: float = 10.0,
    ) -> None:
        self._spawn = spawn
        self._on_output = on_output
        self._on_exit = on_exit
        self._poll = poll_interval
        self._drain_delay = drain_delay
        self._close_timeout = close_timeout
        self._ready_marker = ready_marker  # None이면 신호를 기다리지 않고 입력을 바로 보낸다
        self._ready_signal = None if ready_marker is None else _title_signal(ready_marker)
        self._ready_timeout = ready_timeout
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()

    def open(self, session_id: str, server_id: str, argv: list[str], cols: int, rows: int) -> None:
        if not is_hex_id(session_id):
            raise SessionError(f"잘못된 세션 id: {session_id!r}")
        with self._lock:
            if session_id in self._sessions:
                raise SessionError("이미 열린 세션입니다.")
        pty = self._spawn(argv, rows, cols)
        started = time.monotonic()
        gate = _Gate.OPEN if self._ready_marker is None else _Gate.HOLD
        session = _Session(session_id, server_id, pty, (rows, cols), gate=gate)
        with self._lock:
            self._sessions[session_id] = session
        self._start(session, 0, pty, started)

    def reconnect(self, session_id: str, argv: list[str], cols: int, rows: int) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionError("닫힌 세션입니다.")
            if session.running:
                raise SessionError("아직 실행 중인 세션입니다.")
            session.running = True  # 함께 들어온 다시 연결이 프로세스를 하나 더 띄우지 못하게 먼저 잡는다
            session.generation += 1  # 끝난 프로세스의 늦은 출력은 이제 버린다
            generation = session.generation
            with session.lock:
                # 새 프로세스가 자리 잡기 전에 들어온 입력과 크기는 끝난 프로세스로 보내지 않고 새 프로세스 몫으로 둔다
                session.gate = _Gate.HOLD
                session.held = _Held()
                session.size = (rows, cols)
        separator = f"\r\n\x1b[90m── 다시 연결 ({time.strftime('%H:%M:%S')}) ──\x1b[0m\r\n"
        self._notify(self._on_output, session_id, separator)
        with session.lock:
            if session.closing:
                raise SessionError("닫힌 세션입니다.")
            self._release(session_id, session.pty)  # 끝난 프로세스의 PTY를 놓는다
            rows, cols = session.size  # 그사이 들어온 크기 바꾸기를 따른다
            try:
                pty = self._spawn(argv, rows, cols)
            except Exception:
                session.running = False  # 다시 연결을 다시 누를 수 있게 둔다
                raise
            started = time.monotonic()
            session.pty = pty
            if self._ready_marker is None:
                self._let_input_through(session)
        self._start(session, generation, pty, started)

    def _start(self, session: _Session, generation: int, pty: PtyLike, started: float) -> None:
        tag = session.id[:8]
        threading.Thread(
            target=self._read_loop, args=(session, generation, pty), name=f"hq-read-{tag}", daemon=True
        ).start()
        threading.Thread(
            target=self._watch_loop, args=(session, generation, pty, started), name=f"hq-watch-{tag}", daemon=True
        ).start()

    def _notify(self, callback: Callable[[str, object], None], session_id: str, value: object) -> None:
        try:
            callback(session_id, value)
        except Exception:
            log.warning("화면에 알리지 못함 %s", session_id[:8], exc_info=True)

    def _read_loop(self, session: _Session, generation: int, pty: PtyLike) -> None:
        looking_for = self._ready_signal  # 이 프로세스의 준비 신호를 볼 때까지만 찾는다
        tail = ""  # 앞 출력의 끝부분. 신호가 여러 번에 나뉘어 읽혀도 찾는다
        while True:
            try:
                data = pty.read(4096)
            except EOFError:
                return
            except Exception:
                log.warning("출력 읽기 실패 %s", session.id[:8], exc_info=True)
                return
            if not data:
                continue
            if looking_for is not None:
                signal, longest = looking_for
                seen = tail + data
                if signal.search(seen):
                    looking_for = None
                    self._on_ready(session, generation)
                else:
                    tail = seen[max(0, len(seen) - longest + 1) :]
            if session.generation == generation and not session.closing:
                self._notify(self._on_output, session.id, data)  # 신호도 지우지 않고 그대로 넘긴다

    def _on_ready(self, session: _Session, generation: int) -> None:
        with session.lock:
            if session.generation != generation or session.gate is not _Gate.HOLD:
                return  # 끝난 프로세스의 늦은 출력이거나, 시간이 지나 이미 문을 닫았다
            if session.running and not session.closing:
                self._let_input_through(session)

    def _let_input_through(self, session: _Session) -> None:
        """쥐고 있던 입력을 친 순서대로 보내고 문을 연다. session.lock을 쥐고 부른다."""
        held, session.held = session.held, _Held()
        session.gate = _Gate.OPEN
        for chunk in held.chunks:
            self._send(session, chunk)

    def _watch_loop(self, session: _Session, generation: int, pty: PtyLike, started: float) -> None:
        not_ready = False
        try:
            while pty.isalive():
                if not not_ready and self._ready_expired(session, started):
                    not_ready = True
                    log.warning("준비 신호가 %g초 안에 오지 않아 끝냄 %s", self._ready_timeout, session.id[:8])
                    pty.terminate(force=True)
                    continue
                time.sleep(self._poll)
            time.sleep(self._drain_delay)  # 끝나기 직전 출력을 읽기 스레드가 마저 넘기도록 잠깐 기다린다
            code = pty.exitstatus
        except Exception:
            log.warning("프로세스 상태를 읽지 못함 %s", session.id[:8], exc_info=True)
            code = None
        if not_ready:
            code = NOT_READY_CODE  # 강제로 끝낸 종료 코드 대신 hq가 끝낸 이유를 알린다
        with self._lock:
            if session.generation != generation:
                return
            session.running = False
            closing = session.closing
        with session.lock:  # 관리자 잠금을 놓은 뒤에 잡는다. 이 탭의 쓰기가 막혀 있어도 다른 탭은 멈추지 않는다
            if session.generation == generation:  # 그사이 다시 연결이 새 프로세스 몫으로 비웠으면 그대로 둔다
                session.held = _Held()  # 신호 전에 끝났으면 쥐고 있던 입력은 보내지 않고 버린다
        if not closing:
            self._notify(self._on_exit, session.id, code)

    def _ready_expired(self, session: _Session, started: float) -> bool:
        """신호를 기다린 지 ready_timeout이 지났으면 문을 닫고(쥐고 있던 입력은 버린다) True.

        close()가 끝내는 중인 세션은 건드리지 않는다(닫으려고 끝낸 세션은 보고하지 않는다).
        """
        if session.gate is not _Gate.HOLD or time.monotonic() - started < self._ready_timeout:
            return False
        with session.lock:
            if session.gate is not _Gate.HOLD or session.closing:
                return False
            session.gate = _Gate.SHUT
            session.held = _Held()
            return True

    def _get(self, session_id: str) -> _Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def write(self, session_id: str, data: str) -> None:
        session = self._get(session_id)
        if session is None:
            return
        with session.lock:
            if not session.running or session.closing or session.gate is _Gate.SHUT:
                return
            if session.gate is _Gate.HOLD and not _TERMINAL_REPLIES.fullmatch(data):
                if session.held.add(data):
                    log.warning("준비 신호 전 입력이 %d자를 넘어 나머지를 버림 %s", HELD_INPUT_LIMIT, session_id[:8])
                return
            self._send(session, data)

    def _send(self, session: _Session, data: str) -> None:
        try:
            session.pty.write(data)
        except Exception:
            log.warning("입력을 넘기지 못함 %s", session.id[:8], exc_info=True)

    def resize(self, session_id: str, cols: int, rows: int) -> None:
        session = self._get(session_id)
        if session is None:
            return
        with session.lock:
            session.size = (rows, cols)
            if not session.running or session.closing:
                return
            try:
                session.pty.setwinsize(rows, cols)
            except Exception:
                log.warning("크기를 바꾸지 못함 %s", session_id[:8], exc_info=True)

    def close(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                return
            session.closing = True  # 같은 잠금 안에서 바꿔, 감시 스레드가 이 뒤의 끝남을 보고하지 않게 한다
        with session.lock:  # 다시 연결이 진행 중이면 새 PTY가 자리 잡은 뒤에 그것을 닫는다
            pty = session.pty
        try:
            self._shut_down(session_id, pty)
        except Exception:
            log.warning("세션을 닫지 못함 %s", session_id[:8], exc_info=True)
        finally:
            self._release(session_id, pty)

    def _shut_down(self, session_id: str, pty: PtyLike) -> None:
        """Ctrl+C → exit → close_timeout의 절반에 exit 한 번 더 → close_timeout초가 지나면 강제 종료."""
        if not pty.isalive():
            return
        self._type(session_id, pty, "\x03")  # 실행 중인 명령을 멈춘다
        time.sleep(0.1)
        self._type(session_id, pty, "exit\r")  # 원격에서는 Exit-PSSession, 이어서 안전장치가 세션을 정리하고 끝낸다
        start = time.monotonic()
        if self._ended_by(pty, start + self._close_timeout / 2):
            return
        # Ctrl+C 바로 뒤에 온 입력은 버려질 수 있어 첫 exit이 사라졌을 수 있다. 강제 종료하면 서버 세션이
        # 유휴 시간까지 남으므로 한 번 더 친다. 안전장치는 미리 친 입력을 내 PC에서 실행하지 않으니 남아도 된다.
        self._type(session_id, pty, "exit\r")
        if not self._ended_by(pty, start + self._close_timeout):
            pty.terminate(force=True)

    def _type(self, session_id: str, pty: PtyLike, text: str) -> None:
        try:
            pty.write(text)
        except Exception:
            log.warning("닫기 입력 실패 %s", session_id[:8], exc_info=True)

    @staticmethod
    def _ended_by(pty: PtyLike, deadline: float) -> bool:
        while pty.isalive() and time.monotonic() < deadline:
            time.sleep(0.05)
        return not pty.isalive()

    def _release(self, session_id: str, pty: PtyLike) -> None:
        try:
            pty.close()
        except Exception:
            log.warning("PTY를 놓지 못함 %s", session_id[:8], exc_info=True)

    def close_many(self, session_ids: Iterable[str]) -> None:
        threads = [threading.Thread(target=self.close, args=(sid,), daemon=True) for sid in list(session_ids)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def close_all(self) -> None:
        with self._lock:
            ids = list(self._sessions)
        self.close_many(ids)

    def open_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def running_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._sessions.values() if s.running)

    def sessions_for(self, server_id: str) -> list[str]:
        with self._lock:
            return [sid for sid, s in self._sessions.items() if s.server_id == server_id]

    def server_of(self, session_id: str) -> str | None:
        session = self._get(session_id)
        return session.server_id if session else None
