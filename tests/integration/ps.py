"""통합 테스트용: 실제 Windows PowerShell 5.1로 스크립트를 돌린다."""

import base64
import os
import subprocess
from pathlib import Path

POWERSHELL = str(
    Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
)
_UTF8_OUTPUT = "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"


def run_ps(script: str, *, env: dict[str, str] | None = None, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    encoded = base64.b64encode((_UTF8_OUTPUT + script).encode("utf-16-le")).decode("ascii")
    return subprocess.run(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env={**os.environ, **(env or {})},
        check=False,
    )


def parse_errors(script_text: str, tmp_path: Path) -> list[str]:
    """PowerShell 5.1 파서로 문법 오류 메시지 목록을 돌려준다(없으면 빈 목록)."""
    target = tmp_path / "parse-target.ps1"
    target.write_text(script_text, encoding="utf-8")
    checker = (
        "$errors = $null\n"
        "$text = [IO.File]::ReadAllText($env:HQ_PARSE_FILE, [Text.Encoding]::UTF8)\n"
        "[void][System.Management.Automation.Language.Parser]::ParseInput($text, [ref]$null, [ref]$errors)\n"
        "foreach ($e in $errors) { Write-Output $e.Message }\n"
    )
    result = run_ps(checker, env={"HQ_PARSE_FILE": str(target)})
    assert result.returncode == 0, result.stderr
    return [line for line in result.stdout.splitlines() if line.strip()]
