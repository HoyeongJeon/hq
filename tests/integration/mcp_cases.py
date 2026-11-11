"""조회 검사 시험의 표. 공격 표는 스펙 파일에서 읽어 표가 두 벌이 되지 않게 한다.

여기의 스크립트는 검사에만 넘긴다. 어떤 시험에서도 실행하지 않는다.
"""

from pathlib import Path

SPEC = Path(__file__).resolve().parents[2] / "docs" / "specs" / "2026-11-04-hq-mcp-design.md"

# 스펙 15장의 "파서를 부르지 않고 거절해야 하는 표". 둘째 줄이 두 줄에 걸쳐 있어 스펙에서 줄 단위로 읽지 않는다
NO_PARSE = [
    "using module \\\\z\\share\\m",
    "using `\nmodule \\\\z\\share\\m",
    "using <# c #> module \\\\z\\share\\m",
    "configuration C { Import-DscResource -ModuleName \\\\z\\share\\m }",
]

# 통과해야 하는 표(스펙 15장). Task 9에서 채운다
PASS_SCRIPTS: dict[str, str] = {}


def spec_attacks() -> list[str]:
    """스펙 15장 "조회 검사 공격 표" 바로 아래 powershell 코드 블록의 줄들. 한 줄이 스크립트 하나다."""
    text = SPEC.read_text(encoding="utf-8")
    after = text.split("조회 검사 공격 표", 1)[1]
    before, rest = after.split("```powershell\n", 1)
    assert "```" not in before, "공격 표 바로 아래의 코드 블록이 powershell 블록이 아니다"
    return [line for line in rest.split("```", 1)[0].splitlines() if line.strip()]
