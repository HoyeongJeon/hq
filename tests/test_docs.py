import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE = ROOT / "docs" / "acceptance.md"
SPEC = ROOT / "docs" / "specs" / "2026-10-15-hq-design.md"


def checklist_ids() -> list[str]:
    # 사용자가 체크하면 [ ]가 [x]로 바뀌므로 둘 다 받는다
    text = ACCEPTANCE.read_text(encoding="utf-8")
    return re.findall(r"^- \[[ xX]\] (AC-\d+) ", text, flags=re.MULTILINE)


def spec_ids() -> list[str]:
    text = SPEC.read_text(encoding="utf-8")
    section = text.split("\n## 12. ", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^\| (AC-\d+) \|", section, flags=re.MULTILINE)


def test_acceptance_lists_each_criterion_once_in_order():
    assert checklist_ids() == [f"AC-{n}" for n in range(1, 12)]


def test_acceptance_covers_every_criterion_in_the_spec():
    ids = spec_ids()
    assert ids  # 12장 표를 읽지 못하면 아래 검사가 저절로 통과하므로 먼저 막는다
    listed = checklist_ids()
    assert [i for i in ids if i not in listed] == []
