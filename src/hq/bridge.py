"""화면(창 안의 JS)과 hq 본체 사이의 창구.

pywebview는 js_api로 넘긴 객체의 공개 메서드를 window.pywebview.api 로 내놓는다(함수가 아닌 공개 속성이 있으면 그
안의 메서드까지 내놓는다). 그래서 다른 모듈과 상태는 모두 밑줄(_)로 시작하는 이름에 둔다. 메서드는 받은 값을 확인해
해당 모듈에 넘기고 화면이 쓰기 쉬운 dict를 돌려준다. 판단 로직은 각 모듈에 있고 여기에는 두지 않는다.

pywebview는 화면이 부른 함수마다 스레드를 따로 띄우므로 메서드 여러 개가 동시에 돌 수 있다.

창의 수명에 관한 함수(start_pump, close_requested, shutdown)는 화면에 내놓지 않으려고 메서드가
아니라 모듈 함수로 둔다.
"""

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from hq import excel
from hq.batcher import OutputBatcher
from hq.bootstrap import READY_MARKER, BootstrapParams, ExitReason, build_command, reason_for_exit
from hq.inventory import Inventory, InventoryError, Server
from hq.probe import probe_all
from hq.secret_store import SecretStore
from hq.server_setup import build_server_setup_script
from hq.sessions import SessionError, SessionManager, Spawner, spawn_conpty
from hq.trustedhosts import AddResult, is_trusted, missing_hosts, read_trusted_hosts
from hq.trustedhosts import add_trusted_hosts as add_hosts_elevated
from hq.validation import ValidationError, is_hex_id

log = logging.getLogger(__name__)
PUMP_INTERVAL = 0.016
TEMPLATE_NAME = "hq-서버목록-양식.xlsx"
_PREVIEW_FIELDS = ("name", "nickname", "host", "group", "username")  # 비밀번호와 메모는 미리보기로 보내지 않는다


class Dialogs(Protocol):
    def open_xlsx(self) -> str | None: ...
    def save_xlsx(self, default_name: str) -> str | None: ...


@dataclass
class _Tab:
    """화면의 탭 하나. host는 지금(또는 마지막) 프로세스가 접속한 곳이다(정보를 고쳐도 다시 연결 전까지 그대로)."""

    server_id: str
    host: str
    opening: bool = True  # open_session이 아직 끝나지 않았다
    closed: bool = False  # 여는 중에 닫으라는 요청이 왔다. open_session이 마무리하면서 닫는다


def _fail(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, **extra}


def _size(value: Any, default: int) -> int:
    try:
        return min(max(int(value), 2), 1000)
    except (TypeError, ValueError):
        return default


class Bridge:
    def __init__(
        self,
        *,
        inventory: Inventory,
        secrets: SecretStore,
        emit: Callable[[str], object],
        dialogs: Dialogs,
        destroy: Callable[[], object],
        spawn: Spawner = spawn_conpty,
        probe: Callable[[list[str]], dict[str, bool]] = probe_all,
        read_trusted: Callable[[], list[str] | None] = read_trusted_hosts,
        add_trusted: Callable[[list[str]], AddResult] = add_hosts_elevated,
        load_error: str | None = None,
        session_options: dict[str, Any] | None = None,
    ) -> None:
        self._inventory = inventory
        self._secrets = secrets
        self._emit_js = emit
        self._dialogs = dialogs
        self._destroy = destroy
        self._probe = probe
        self._read_trusted = read_trusted
        self._add_trusted = add_trusted
        self._load_error = load_error
        self._batcher = OutputBatcher()
        # 탭의 입력은 부트스트랩이 안전장치를 걸었다는 신호가 온 뒤에야 보낸다(spec 7.3)
        options = {"ready_marker": READY_MARKER, **(session_options or {})}
        self._sessions = SessionManager(spawn=spawn, on_output=self._batcher.add, on_exit=self._on_exit, **options)
        self._previews = excel.PreviewStore()
        self._tabs: dict[str, _Tab] = {}
        self._lock = threading.Lock()  # _tabs와 그 안의 _Tab을 지킨다
        self._emit_lock = threading.Lock()  # 출력 묶음과 끝남 알림이 순서대로 화면에 가게 한다
        self._stop = threading.Event()  # shutdown: 펌프를 멈추고 새 탭을 받지 않는다
        self._quitting = False

    # ---------- 서버 목록 ----------
    def list_servers(self) -> dict[str, Any]:
        if blocked := self._blocked():
            return blocked
        return {"ok": True, "servers": [self._public(s) for s in self._inventory.servers]}

    def add_server(self, info: dict[str, Any], password: str) -> dict[str, Any]:
        if blocked := self._blocked():
            return blocked
        if not isinstance(password, str) or not password:
            return _fail("PW를 입력하세요.")
        try:
            server = self._inventory.add(info)
        except ValidationError as exc:
            return _fail(str(exc))
        try:
            self._secrets.save(server.id, password)
            self._inventory.save()
        except (OSError, InventoryError) as exc:
            self._inventory.remove(server.id)
            self._secrets.delete(server.id)
            return _fail(f"저장하지 못했습니다: {exc}")
        log.info("서버 추가 %s %s", server.id, server.host)
        return {"ok": True, "server": self._public(server)}

    def update_server(self, server_id: str, info: dict[str, Any]) -> dict[str, Any]:
        if blocked := self._blocked():
            return blocked
        try:
            server = self._inventory.update(server_id, info)
            self._inventory.save()
        except KeyError:
            return _fail("목록에 없는 서버입니다.")
        except ValidationError as exc:
            return _fail(str(exc))
        except (OSError, InventoryError) as exc:
            return _fail(f"저장하지 못했습니다: {exc}")
        return {"ok": True, "server": self._public(server)}

    def set_password(self, server_id: str, password: str) -> dict[str, Any]:
        if not isinstance(password, str) or not password:
            return _fail("비밀번호를 입력하세요.")
        try:
            self._inventory.get(server_id)
            self._secrets.save(server_id, password)
        except KeyError:
            return _fail("목록에 없는 서버입니다.")
        except (OSError, ValueError) as exc:
            return _fail(f"저장하지 못했습니다: {exc}")
        return {"ok": True}

    def delete_server(self, server_id: str) -> dict[str, Any]:
        if blocked := self._blocked():
            return blocked
        try:
            self._inventory.get(server_id)
        except KeyError:
            return _fail("목록에 없는 서버입니다.")
        closed = self._close_tabs(lambda _sid, tab: tab.server_id == server_id)
        self._inventory.remove(server_id)
        self._secrets.delete(server_id)
        try:
            self._inventory.save()
        except (OSError, InventoryError) as exc:
            return _fail(f"저장하지 못했습니다: {exc}")
        log.info("서버 삭제 %s", server_id)
        return {"ok": True, "closed_sessions": closed}

    def probe(self) -> dict[str, Any]:
        servers = self._inventory.servers
        result = self._probe_hosts([s.host for s in servers])
        if result is None:
            return _fail("서버 상태를 확인하지 못했습니다.")
        return {"ok": True, "status": {s.id: bool(result.get(s.host, False)) for s in servers}}

    # ---------- 엑셀 ----------
    def save_excel_template(self) -> dict[str, Any]:
        path = self._dialogs.save_xlsx(TEMPLATE_NAME)
        if not path:
            return {"ok": False, "cancelled": True}
        try:
            excel.write_template(Path(path))
        except OSError as exc:
            return _fail(f"저장하지 못했습니다(파일이 열려 있으면 닫으세요): {exc}")
        return {"ok": True, "path": path}

    def preview_excel(self) -> dict[str, Any]:
        if blocked := self._blocked():
            return blocked
        path = self._dialogs.open_xlsx()
        if not path:
            return {"ok": False, "cancelled": True}
        try:
            preview = excel.build_preview(Path(path), self._inventory)
        except excel.ExcelError as exc:
            return _fail(str(exc))  # 메시지만 쓴다. 예외의 traceback은 앞 행의 비밀번호를 쥐고 있다
        self._previews.put(preview)
        counts = {"new": 0, "existing": 0, "error": 0}
        rows = []
        for row in preview.rows:
            counts[row.status] += 1
            shown = {key: row.fields.get(key, "") for key in _PREVIEW_FIELDS}
            rows.append({"row": row.row, "status": row.status, "error": row.error, **shown})
        return {"ok": True, "preview_id": preview.id, "rows": rows, "counts": counts}

    def apply_excel(self, preview_id: str, overwrite: bool) -> dict[str, Any]:
        if blocked := self._blocked():
            return blocked
        preview = self._previews.take(str(preview_id))
        if preview is None:
            return _fail("미리보기가 만료되었습니다. 파일을 다시 고르세요.")
        before = {s.id for s in self._inventory.servers}
        try:
            result = excel.apply_preview(preview, self._inventory, self._secrets, overwrite=bool(overwrite))
        except Exception as exc:  # 어떤 이유로 멈췄든 반쯤 등록한 것을 남기지 않는다
            message = f"등록하지 못했습니다: {exc}"
            log.warning("엑셀 등록 실패. 서버 목록을 파일에서 다시 읽음", exc_info=True)
        else:
            log.info("엑셀 등록 %s", asdict(result))
            return {"ok": True, **asdict(result)}
        self._undo_apply(before)
        return _fail(message)

    # ---------- 초기 설정 ----------
    def missing_trusted_hosts(self) -> dict[str, Any]:
        entries = self._read_trusted()
        if entries is None:
            return {"ok": True, "readable": False, "missing": []}
        hosts = [s.host for s in self._inventory.servers]
        return {"ok": True, "readable": True, "missing": missing_hosts(hosts, entries)}

    def add_trusted_hosts(self) -> dict[str, Any]:
        hosts = [s.host for s in self._inventory.servers]
        entries = self._read_trusted()
        wanted = hosts if entries is None else missing_hosts(hosts, entries)
        result = self._add_trusted(wanted)
        if result is AddResult.OK:
            return {"ok": True}
        if result is AddResult.CANCELLED:
            return _fail("관리자 권한 요청을 취소했습니다.", cancelled=True)
        return _fail(
            "TrustedHosts를 설정하지 못했습니다. 이 PC의 WinRM 서비스가 '사용 안 함'이면 서비스에서 '수동'으로 바꾼 뒤 "
            "다시 시도하세요. 그래도 안 되면 관리자 권한 PowerShell에서 직접 설정하세요."
        )

    def server_setup_script(self, server_id: str, restrict: bool) -> dict[str, Any]:
        try:
            server = self._inventory.get(server_id)
        except KeyError:
            return _fail("목록에 없는 서버입니다.")
        ips = self._inventory.admin_ips if restrict else []
        if restrict and not ips:
            return _fail("설정 화면에서 관리자 PC IP를 먼저 저장하세요.")
        try:
            return {"ok": True, "script": build_server_setup_script(server.username, ips)}
        except ValidationError as exc:
            return _fail(str(exc))

    def get_settings(self) -> dict[str, Any]:
        return {"ok": True, "admin_ips": self._inventory.admin_ips}

    def save_settings(self, admin_ips: list[str]) -> dict[str, Any]:
        if blocked := self._blocked():
            return blocked
        try:
            self._inventory.set_admin_ips([str(ip) for ip in admin_ips or []])
            self._inventory.save()
        except ValidationError as exc:
            return _fail(str(exc))
        except (OSError, InventoryError) as exc:
            return _fail(f"저장하지 못했습니다: {exc}")
        return {"ok": True, "admin_ips": self._inventory.admin_ips}

    # ---------- 세션 ----------
    def open_session(self, session_id: str, server_id: str, cols: int, rows: int) -> dict[str, Any]:
        if not is_hex_id(session_id):
            return _fail("잘못된 세션 id입니다.")
        try:
            server = self._inventory.get(server_id)
        except KeyError:
            return _fail("목록에 없는 서버입니다.")
        tab = _Tab(server.id, server.host)
        with self._lock:
            if self._stop.is_set():
                return _fail("HQ를 끝내는 중입니다.")
            # 화면은 탭마다 새 id를 만든다. 같은 id로 두 번 띄우면 그중 하나는 아무도 닫지 못하는 프로세스가 된다
            if session_id in self._tabs:
                return _fail("이미 열린 탭입니다.")
            self._tabs[session_id] = tab
        result = self._precheck(server) or self._start(session_id, tab, server, cols, rows)
        with self._lock:
            tab.opening = False
            closed = tab.closed
            if closed or not result["ok"]:
                del self._tabs[session_id]  # 자리를 맡은 동안에는 누구도 지우지 않는다(_close_tabs 참고)
        if closed:
            self._sessions.close(session_id)  # 띄우는 사이에 닫혔다. 띄우지 않았으면 아무 일도 없다
            self._batcher.discard(session_id)
            return {"ok": False, "cancelled": True}
        return result

    def reconnect(self, session_id: str, cols: int, rows: int) -> dict[str, Any]:
        session_id = str(session_id)
        with self._lock:
            tab = self._tabs.get(session_id)
        if tab is None:
            return _fail("닫힌 탭입니다.")
        try:
            server = self._inventory.get(tab.server_id)
        except KeyError:
            return _fail("이 서버는 목록에서 지워졌습니다.")
        if problem := self._precheck(server):
            return problem
        with self._lock:
            tab.host = server.host  # 새 프로세스는 지금 서버 정보로 접속한다
        try:
            self._sessions.reconnect(session_id, self._command_for(server), _size(cols, 80), _size(rows, 24))
        except SessionError as exc:
            return _fail(f"다시 연결하지 못했습니다: {exc}")
        except OSError as exc:
            return self._spawn_failed(session_id, server, exc)
        log.info("다시 연결 %s 서버 %s %s", session_id[:8], server.id, server.host)
        return {"ok": True}

    def send_input(self, session_id: str, data: str) -> None:
        # 받은 입력 하나를 쓰기 하나로 넘긴다. 묶으면 터미널 응답과 키가 섞여 준비 신호 전에 쥐고 있게 된다
        if isinstance(data, str):
            self._sessions.write(str(session_id), data)

    def resize(self, session_id: str, cols: int, rows: int) -> None:
        self._sessions.resize(str(session_id), _size(cols, 80), _size(rows, 24))

    def close_session(self, session_id: str) -> dict[str, Any]:
        session_id = str(session_id)
        self._close_tabs(lambda sid, _tab: sid == session_id)
        return {"ok": True}

    def quit(self) -> None:
        self._quitting = True
        try:
            shutdown(self)
        finally:
            self._destroy()

    # ---------- 내부 ----------
    def _blocked(self) -> dict[str, Any] | None:
        """서버 목록 파일을 읽지 못했으면 그 안내. 그때는 목록을 보이거나 바꾸지 않는다(깨진 파일을 덮어쓰지 않게)."""
        return _fail(self._load_error) if self._load_error else None

    def _public(self, server: Server) -> dict[str, Any]:
        return {
            "id": server.id,
            "name": server.name,
            "nickname": server.nickname,
            "host": server.host,
            "group": server.group,
            "username": server.username,
            "memo": server.memo,
            "has_password": self._secrets.has(server.id),
        }

    def _undo_apply(self, before: set[str]) -> None:
        """멈춘 엑셀 등록이 남긴 것을 버린다. 서버 목록을 파일에서 다시 읽고, 이번에 새로 만든 서버의 비밀번호 파일을
        지운다. 덮어쓴 서버의 비밀번호 파일은 되돌릴 수 없다(hq는 비밀번호를 풀지 않는다)."""
        added = {s.id for s in self._inventory.servers} - before
        try:
            self._inventory.load()
        except InventoryError as exc:
            self._load_error = str(exc)
            log.warning("서버 목록을 다시 읽지 못함", exc_info=True)
        for server_id in added - {s.id for s in self._inventory.servers}:
            try:
                self._secrets.delete(server_id)
            except OSError:
                log.warning("비밀번호 파일을 지우지 못함 %s", server_id, exc_info=True)

    def _command_for(self, server: Server) -> list[str]:
        params = BootstrapParams(
            host=server.host,
            username=server.username,
            nickname=server.display_name,
            secret_path=str(self._secrets.path_for(server.id)),
        )
        return build_command(params)

    def _precheck(self, server: Server) -> dict[str, Any] | None:
        if not self._secrets.has(server.id):
            return _fail("저장된 비밀번호가 없습니다.", need="password")
        entries = self._read_trusted()
        if entries is not None and not is_trusted(server.host, entries):
            return _fail("초기 설정 필요: 이 PC의 TrustedHosts에 서버가 없습니다.", need="trustedhosts")
        online = self._probe_hosts([server.host])
        # 확인하지 못했으면 TrustedHosts를 읽지 못했을 때처럼 건너뛰고 접속 스크립트의 오류 분류에 맡긴다
        if online is not None and not online.get(server.host, False):
            return _fail(f"서버 응답 없음: {server.host}:5985", need="unreachable")
        return None

    def _probe_hosts(self, hosts: list[str]) -> dict[str, bool] | None:
        """5985 확인을 새 스레드에서 돌리고 기다린다. 확인하지 못하면 None.

        probe_all은 자기 이벤트 루프를 돌리므로, 루프가 이미 도는 스레드나 창 스레드에서 부르면 안 된다.
        """
        found: dict[str, dict[str, bool]] = {}

        def run() -> None:
            try:
                found["result"] = self._probe(hosts)
            except Exception:
                log.warning("서버 상태 확인 실패", exc_info=True)

        worker = threading.Thread(target=run, name="hq-probe", daemon=True)
        worker.start()
        worker.join()
        return found.get("result")

    def _start(self, session_id: str, tab: _Tab, server: Server, cols: int, rows: int) -> dict[str, Any]:
        with self._lock:
            if tab.closed:
                return {"ok": False, "cancelled": True}  # 사전 확인 중에 닫혔다. 띄우지 않는다
        try:
            self._sessions.open(session_id, server.id, self._command_for(server), _size(cols, 80), _size(rows, 24))
        except SessionError as exc:
            return _fail(f"탭을 열지 못했습니다: {exc}")
        except OSError as exc:
            return self._spawn_failed(session_id, server, exc)
        log.info("세션 열림 %s 서버 %s %s", session_id[:8], server.id, server.host)
        return {"ok": True}

    @staticmethod
    def _spawn_failed(session_id: str, server: Server, exc: OSError) -> dict[str, Any]:
        """탭의 powershell.exe를 띄우지 못했을 때(예: 백신이 막아 액세스 거부). except 블록 안에서 부른다."""
        log.warning("PowerShell을 띄우지 못함 %s 서버 %s %s", session_id[:8], server.id, server.host, exc_info=True)
        return _fail(
            f"PowerShell을 띄우지 못했습니다({exc}). "
            "백신 프로그램이 실행을 막았을 수 있으니 백신의 차단 기록을 확인하세요."
        )

    def _close_tabs(self, match: Callable[[str, _Tab], bool]) -> list[str]:
        """맞는 탭을 닫고 그 id 목록을 돌려준다.

        여는 중인 탭은 표시만 하고 자리를 남긴다. open_session이 마무리하면서 닫고 자리를 지운다. 그때까지 같은 id로
        다시 열 수 없다.
        """
        with self._lock:
            ids = [sid for sid, tab in self._tabs.items() if match(sid, tab)]
            for sid in ids:
                if self._tabs[sid].opening:
                    self._tabs[sid].closed = True
                else:
                    del self._tabs[sid]
        self._sessions.close_many(ids)
        for sid in ids:
            self._batcher.discard(sid)
        return ids

    def _emit(self, script: str) -> None:
        try:
            self._emit_js(script)
        except Exception:
            log.warning("화면에 보내지 못함(창이 닫히는 중일 수 있음)", exc_info=True)

    def _send_output(self) -> bool:
        """모아 둔 출력을 한 묶음 보낸다. 보낸 것이 있으면 True. _emit_lock을 쥐고 부른다."""
        batch = self._batcher.drain()
        if batch:
            self._emit(f"window.hq.onOutput({json.dumps(batch)})")
        return bool(batch)

    def _flush(self) -> None:
        with self._emit_lock:
            self._send_output()

    def _trusted_now(self, host: str) -> bool:
        # 끝남 알림을 보내는 길이라 여기서 예외가 나면 탭이 끝난 줄 모르게 된다. 읽지 못하면 아니라고 본다
        try:
            entries = self._read_trusted()
        except Exception:
            log.warning("TrustedHosts를 읽지 못함", exc_info=True)
            return False
        return entries is not None and is_trusted(host, entries)

    def _on_exit(self, session_id: str, code: int | None) -> None:
        with self._lock:
            tab = self._tabs.get(session_id)
            server_id, host = (tab.server_id, tab.host) if tab else ("?", "?")
        reason = reason_for_exit(code)
        if reason is ExitReason.NOT_TRUSTED and tab is not None and self._trusted_now(host):
            # 1312(0x8009030e)는 TrustedHosts에 없을 때뿐 아니라 계정 정보가 틀렸을 때도 난다(docs/verification.md V3)
            reason = ExitReason.AUTH_FAILED
        log.info("세션 끝남 %s 서버 %s %s: %s (종료 코드 %s)", session_id[:8], server_id, host, reason.value, code)
        with self._emit_lock:
            while self._send_output():  # 끝나기 전 출력이 먼저 화면에 가도록
                pass
            self._emit(f"window.hq.onSessionEnded({json.dumps(session_id)}, {json.dumps(reason.value)})")


def start_pump(bridge: Bridge) -> None:
    """출력을 약 16ms마다 묶어 화면에 보낸다. pywebview가 창을 띄운 뒤 부른다. shutdown이 멈춘다."""

    def loop() -> None:
        while not bridge._stop.wait(PUMP_INTERVAL):
            try:
                bridge._flush()
            except Exception:
                log.warning("출력을 화면에 보내지 못함", exc_info=True)

    threading.Thread(target=loop, name="hq-pump", daemon=True).start()


def close_requested(bridge: Bridge) -> bool:
    """창을 닫아도 되면 True. 열린 탭이 있으면 화면에 확인을 부탁하고 False(닫기 취소)."""
    if bridge._quitting:
        return True
    with bridge._lock:
        count = len(bridge._tabs)
    if count == 0:
        return True
    # 닫기 이벤트는 창 스레드에서 오고 evaluate_js는 창 스레드를 기다리므로 화면 호출은 다른 스레드에서 한다
    message = f"window.hq.onCloseRequested({count})"
    threading.Thread(target=bridge._emit, args=(message,), name="hq-close", daemon=True).start()
    return False


def shutdown(bridge: Bridge) -> None:
    """펌프를 멈추고 새 탭을 받지 않으며, 모든 탭을 동시에 닫는다(최대 close_timeout초). 여러 번 불러도 된다."""
    bridge._stop.set()
    bridge._close_tabs(lambda _sid, _tab: True)
