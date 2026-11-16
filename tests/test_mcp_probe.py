import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from hq.mcp.schema import tool_shapes

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tools/mcp/probe_server.py"


def _load_probe():
    # tools/mcp는 패키지가 아니므로 파일 경로에서 읽는다
    spec = importlib.util.spec_from_file_location("probe_server", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load_probe()


class _NoWait(threading.Event):
    """기다리라는 시간만 적고 바로 돌아온다. 테스트가 130초를 기다리지 않는다."""

    def __init__(self) -> None:
        super().__init__()
        self.waited: list[float | None] = []

    def wait(self, timeout: float | None = None) -> bool:
        self.waited.append(timeout)
        return False


def test_the_probe_has_the_same_names_shapes_and_meta_as_the_real_tools():
    tools, _ = probe.build()
    shapes = tool_shapes()
    assert [t.name for t in tools] == ["list_servers", "run_query", "run_script"]
    for t in tools:
        assert (t.input_schema, t.annotations, t.meta) == (
            shapes[t.name]["input_schema"],
            shapes[t.name]["annotations"],
            shapes[t.name]["meta"],
        )


def test_the_probe_says_it_is_a_fake_and_lists_one_documentation_server():
    tools, handlers = probe.build()
    assert {t.description for t in tools} == {"체크포인트 A용 가짜"}
    assert json.loads(handlers["list_servers"]({}, threading.Event()).text) == {
        "servers": [{"target": "시험01 (192.0.2.10)", "host": "192.0.2.10"}]
    }


def test_the_probe_echoes_arguments_and_serves_the_big_text():
    _, handlers = probe.build()
    args = {"targets": ["시험01 (192.0.2.10)"], "script": "Get-Date", "reason": "시험"}
    assert json.loads(handlers["run_script"](args, threading.Event()).text) == args
    assert handlers["run_query"]({"targets": ["x"], "script": "big"}, threading.Event()).text == probe.BIG_TEXT
    assert len(probe.BIG_TEXT) == 7400 and all(ord(c) > 127 for c in probe.BIG_TEXT)


def test_big_slow_waits_130_seconds_on_the_cancel_signal():
    _, handlers = probe.build()
    cancel = _NoWait()
    assert handlers["run_query"]({"targets": ["x"], "script": "big-slow"}, cancel).text == probe.BIG_TEXT
    assert cancel.waited == [130]


def test_big_slow_stops_waiting_once_cancelled():
    _, handlers = probe.build()
    cancel = threading.Event()
    cancel.set()
    started = time.monotonic()
    handlers["run_script"]({"targets": ["x"], "script": "big-slow", "reason": "시험"}, cancel)
    assert time.monotonic() - started < 5


def test_the_probe_logs_the_protocol_version_and_argument_sizes_in_its_working_folder(tmp_path):
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "run_script",
                "arguments": {"targets": ["시험01 (192.0.2.10)"], "script": "#" * 20000, "reason": "시험"},
            },
        },
    ]
    stdin = b"".join(json.dumps(m, ensure_ascii=False).encode() + b"\n" for m in messages)
    done = subprocess.run(
        [sys.executable, str(PROBE)], input=stdin, cwd=tmp_path, capture_output=True, timeout=30, check=True
    )
    assert [json.loads(line)["id"] for line in done.stdout.splitlines()] == [1, 2]  # 표준 출력에는 MCP 응답만
    log = (tmp_path / "probe-log.txt").read_text(encoding="utf-8").splitlines()
    assert len(log) == 2
    assert "protocolVersion=2025-06-18" in log[0]
    assert "run_script" in log[1] and '"script": 20000' in log[1] and '"reason": 2' in log[1]


def test_the_probe_has_no_code_that_could_reach_a_server():
    text = (ROOT / "tools/mcp/probe_server.py").read_text(encoding="utf-8")
    for word in ("subprocess", "socket", "hq.inventory", "hq.secret_store", "runner", "New-PSSession"):
        assert word not in text, word


def test_the_setup_script_copies_the_settings_and_registers_only_the_probe():
    text = (ROOT / "tools/mcp/setup-probe.ps1").read_text(encoding="utf-8-sig")
    assert "C:\\dev\\hq-mcp-probe" in text and "tools\\mcp\\probe_server.py" in text and "$PSScriptRoot" in text
    assert "claude mcp add --scope local --transport stdio hq --" in text
    assert "python -m hq.mcp" not in text  # 진짜 서버를 등록하지 않는다


def test_the_remove_script_unregisters_the_probe_and_deletes_what_setup_made():
    text = (ROOT / "tools/mcp/remove-probe.ps1").read_text(encoding="utf-8-sig")
    assert "claude mcp remove hq --scope local" in text
    assert "C:\\dev\\hq-mcp-probe" in text and "hq-probe-outside.txt" in text


@pytest.mark.parametrize("start", ["probe", "probe/.claude", "."])
def test_the_remove_script_deletes_the_folder_even_when_started_inside_it(tmp_path, start):
    # Windows는 어느 프로세스의 현재 폴더인 폴더를 지우지 못하게 한다. A-3에서 폴더 안에서 실행해 폴더가 남았다
    # (2026-11-16). 스크립트의 폴더 경로를 tmp로 바꾼 사본을 돌린다. PATH를 좁혀 진짜 claude 대신 tmp의 claude.cmd가
    # 불리게 하므로 Claude Code의 설정은 건드리지 않는다
    probe_dir = tmp_path / "probe"
    (probe_dir / ".claude").mkdir(parents=True)
    (probe_dir / "probe-log.txt").write_text("x", encoding="utf-8")
    temp = tmp_path / "temp"
    temp.mkdir()
    (temp / "hq-probe-outside.txt").write_text("outside", encoding="ascii")
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "claude.cmd").write_text('@echo %*> "%~dp0args.txt"\r\n@cd>> "%~dp0args.txt"\r\n@exit /b 0\r\n', "ascii")
    text = (ROOT / "tools/mcp/remove-probe.ps1").read_text(encoding="utf-8-sig")
    assert text.count("'C:\\dev\\hq-mcp-probe'") == 1
    copy = tmp_path / "remove-probe.ps1"
    copy.write_text(text.replace("'C:\\dev\\hq-mcp-probe'", f"'{probe_dir}'"), encoding="utf-8-sig")
    system = Path(os.environ["SYSTEMROOT"]) / "System32"
    env = {**os.environ, "TEMP": str(temp), "TMP": str(temp), "PATH": f"{stub};{system}"}
    done = subprocess.run(
        [str(system / "WindowsPowerShell/v1.0/powershell.exe"), "-NoLogo", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-File", str(copy)],
        cwd=tmp_path / start, env=env, capture_output=True, timeout=60,
    )  # fmt: skip
    called = (stub / "args.txt").read_text(encoding="ascii", errors="replace").splitlines()
    assert called[0].strip() == "mcp remove hq --scope local"
    assert called[1].lower().endswith("\\probe")  # local 범위 등록은 그 폴더에서 지운다
    assert done.returncode == 0, done.stderr.decode("cp949", "replace")
    assert not probe_dir.exists()
    assert not (temp / "hq-probe-outside.txt").exists()
