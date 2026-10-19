"""docs/acceptance.md AC-9의 평문 검사 명령을 그 글자 그대로 Windows PowerShell 5.1로 돌려 본다.

Read-Host -AsSecureString은 표준 입력이 아니라 콘솔에서 읽으므로, 같은 이름의 함수로 가려 시험용 값을 넘긴다.
LOCALAPPDATA는 tmp_path로 바꿔 사용자의 진짜 %LOCALAPPDATA%\\hq는 건드리지 않는다.
"""

import re
from pathlib import Path

import pytest
from ps import run_ps

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
TEST_PASSWORD = "Hq-Ac9-Test!"  # 시험용 값
BLOCK_VARIABLES = ("pw", "plain", "files", "hits", "enc")


def plaintext_check_block() -> str:
    """AC-9의 Read-Host가 든 powershell 블록. 목록 들여쓰기(두 칸)를 벗긴다."""
    text = (ROOT / "docs" / "acceptance.md").read_text(encoding="utf-8")
    blocks = re.findall(r"^  ```powershell\n(.*?)^  ```$", text, flags=re.S | re.M)
    found = [b for b in blocks if "Read-Host -AsSecureString" in b]
    assert len(found) == 1
    return "\n".join(line.removeprefix("  ") for line in found[0].splitlines())


def run_block(tmp_path: Path, typed: str) -> str:
    """Read-Host가 typed를 받은 것처럼 블록을 돌리고, 출력 뒤에 블록 밖에 남은 변수 이름을 붙여 돌려준다."""
    block = tmp_path / "block.ps1"
    block.write_text(plaintext_check_block(), encoding="utf-8")
    script = (
        "function Read-Host { param([Parameter(Position = 0)] $Prompt, [switch] $AsSecureString)\n"
        "  $s = New-Object Security.SecureString\n"
        f"  foreach ($c in '{typed}'.ToCharArray()) {{ $s.AppendChar($c) }}\n"
        "  $s }\n"
        "Invoke-Expression ([IO.File]::ReadAllText($env:HQ_BLOCK, [Text.Encoding]::UTF8))\n"
        f"'남은 변수:' + ((@({', '.join(repr(v) for v in BLOCK_VARIABLES)}) |"
        " Where-Object { Test-Path \"variable:$_\" }) -join ',')\n"
    )
    result = run_ps(script, env={"LOCALAPPDATA": str(tmp_path / "local"), "HQ_BLOCK": str(block)})
    assert result.returncode == 0, result.stderr
    return result.stdout + result.stderr


def make_data(tmp_path: Path) -> dict[str, Path]:
    hq = tmp_path / "local" / "hq"
    (hq / "logs").mkdir(parents=True)
    files = {"utf8": hq / "inventory.json", "utf16": hq / "logs" / "hq.log", "clean": hq / "clean.txt"}
    files["utf8"].write_text(f'{{"memo": "{TEST_PASSWORD}"}}', encoding="utf-8")
    files["utf16"].write_text(f"pw={TEST_PASSWORD}", encoding="utf-16-le")
    files["clean"].write_text("비밀번호 없음", encoding="utf-8")
    return files


def test_plaintext_check_finds_utf8_and_utf16_copies_and_leaves_no_variables(tmp_path):
    files = make_data(tmp_path)
    out = run_block(tmp_path, TEST_PASSWORD)
    assert "검사한 파일: 3개" in out
    assert str(files["utf8"]) in out
    assert str(files["utf16"]) in out
    assert str(files["clean"]) not in out
    assert TEST_PASSWORD not in out  # 맞은 줄의 내용은 내보내지 않는다
    leftover = next(line for line in out.splitlines() if line.startswith("남은 변수:"))
    assert leftover == "남은 변수:"


def test_plaintext_check_refuses_to_report_when_nothing_was_typed(tmp_path):
    # 빈 입력은 아무것도 검사하지 않으므로 파일 수와 함께 '경로 없음'(통과처럼 보이는 출력)을 내면 안 된다
    make_data(tmp_path)
    out = run_block(tmp_path, "")
    assert "비밀번호를 입력하지 않아 검사하지 않았다" in out
    assert "검사한 파일" not in out
    assert "Pattern" not in out


def test_plaintext_check_is_one_statement_so_a_pasting_console_waits_for_all_of_it(tmp_path):
    # 붙여 넣은 줄을 한 줄씩 받는 콘솔(PSReadLine 포함)은 입력이 덜 끝났을 때(IncompleteInput)만 다음 줄을 기다린다.
    # 앞 줄들만으로 끝난 명령이 되면 Read-Host가 붙여 넣은 다음 줄을 비밀번호로 받아 버린다
    block = tmp_path / "block.ps1"
    block.write_text(plaintext_check_block(), encoding="utf-8")
    script = (
        "$lines = [IO.File]::ReadAllLines($env:HQ_BLOCK, [Text.Encoding]::UTF8)\n"
        "for ($k = 1; $k -le $lines.Count; $k++) {\n"
        "  $errors = $null\n"
        "  $ast = [Management.Automation.Language.Parser]::ParseInput(($lines[0..($k - 1)] -join \"`n\"),"
        " [ref]$null, [ref]$errors)\n"
        "  $state = if (-not $errors) { 'complete' } elseif (@($errors | Where-Object IncompleteInput).Count) {"
        " 'incomplete' } else { 'error' }\n"
        "  '{0}:{1}:{2}' -f $k, $state, $ast.EndBlock.Statements.Count\n"
        "}\n"
    )
    result = run_ps(script, env={"HQ_BLOCK": str(block)})
    assert result.returncode == 0, result.stderr
    rows = [line.split(":") for line in result.stdout.split()]
    *prefixes, whole = rows
    assert [state for _, state, _ in prefixes] == ["incomplete"] * len(prefixes)
    assert whole[1:] == ["complete", "1"]
