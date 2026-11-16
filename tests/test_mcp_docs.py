import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MCP_SPEC = ROOT / "docs/specs/2026-11-04-hq-mcp-design.md"
ACCEPT = ROOT / "docs/mcp-acceptance.md"


def _checkpoint_b() -> str:
    return ACCEPT.read_text(encoding="utf-8").split("\n## 체크포인트 B", 1)[1]


def _manual_item(item_id: str) -> str:
    """수동 확인 문서에서 그 항목부터 다음 항목이나 다음 절 앞까지의 글."""
    text = ACCEPT.read_text(encoding="utf-8")
    start = re.search(rf"^- \[[ xX]\] {item_id} ", text, flags=re.M).start()
    end = re.compile(r"^(?:- \[[ xX]\] (?:A|MCP)-\d+ |#)", flags=re.M).search(text, start + 1)
    return text[start : end.start() if end else len(text)]


def _spec_section(heading: str) -> str:
    """MCP 스펙에서 heading으로 시작하는 절(다음 같은 깊이 이상의 제목 앞까지)."""
    text = MCP_SPEC.read_text(encoding="utf-8")
    level = heading.split(" ", 1)[0]
    body = text.split(f"\n{heading}", 1)[1]
    return re.split(rf"\n#{{1,{len(level)}}} ", body, maxsplit=1)[0]


def test_the_checklist_lists_a1_to_a5_and_mcp1_to_mcp11_once_each_in_order():
    ids = re.findall(r"^- \[[ xX]\] ((?:A|MCP)-\d+) ", ACCEPT.read_text(encoding="utf-8"), flags=re.M)
    assert ids == [f"A-{n}" for n in range(1, 6)] + [f"MCP-{n}" for n in range(1, 12)]


def test_every_manual_check_in_the_spec_is_on_the_checklist():
    spec_ids = re.findall(r"^- \*\*(MCP-\d+)\*\*", MCP_SPEC.read_text(encoding="utf-8"), flags=re.M)
    assert spec_ids == [f"MCP-{n}" for n in range(1, 12)]


def test_the_checklist_carries_each_spec_sentence_word_for_word():
    spec = dict(re.findall(r"^- \*\*(MCP-\d+)\*\* (.+)$", MCP_SPEC.read_text(encoding="utf-8"), flags=re.M))
    assert spec
    for item_id, sentence in spec.items():
        assert re.match(rf"- \[[ xX]\] {item_id} {re.escape(sentence)}", _manual_item(item_id)), item_id


def test_mcp2_also_looks_for_write_host_output_on_2012_r2():
    assert "2012 R2에서 `Write-Host` 출력이 `output`에 오는지도 본다" in _manual_item("MCP-2")


def test_the_readme_says_what_the_mcp_server_sends_and_where_the_script_record_is():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for word in (
        "python -m hq.mcp",
        "hq-ops",
        "mcp-audit",
        "hq.log",
        "docs/specs/2026-11-04-hq-mcp-design.md",
        "not a security boundary",
    ):
        assert word in text, word


def test_the_readme_puts_the_mcp_section_before_security_and_scopes_the_log_sentence_to_hq_log():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert text.index("## Claude Code (MCP)") < text.index("## Security")
    assert "`hq.log` never holds what you type or what a server prints" in text
    assert "Logs never contain passwords or terminal input and output" not in text


def test_the_base_spec_points_to_the_mcp_spec_in_three_places():
    text = (ROOT / "docs/specs/2026-10-15-hq-design.md").read_text(encoding="utf-8")
    assert text.count("2026-11-04-hq-mcp-design.md") >= 3


def test_public_files_hold_only_documentation_addresses():
    # 110.0.1.21은 Task 6의 경계 시험 값
    allowed = re.compile(r"^(10\.0\.1\.\d+|192\.0\.2\.\d+|198\.51\.100\.\d+|127\.0\.0\.1|110\.0\.1\.21)$")
    files = [
        MCP_SPEC,
        ACCEPT,
        ROOT / "README.md",
        ROOT / "docs/plans/2026-11-10-hq-mcp.md",
        *(ROOT / "src/hq/mcp").rglob("*"),
        *(ROOT / "src/hq/ps").glob("mcp-*.ps1"),
        *(ROOT / "tools/mcp").rglob("*"),
        *ROOT.glob("tests/test_mcp_*.py"),
        *ROOT.glob("tests/integration/*mcp*.py"),
    ]
    for path in (f for f in files if f.suffix in {".py", ".json", ".ps1", ".md"}):
        text = path.read_text(encoding="utf-8", errors="replace")
        # <Objs Version="1.1.0.1">는 PowerShell CLIXML의 버전 번호다(test_mcp_psrun.py). 주소가 아니다
        for ip in re.findall(r'(?<![\d.])(?<!Version=")\d{1,3}(?:\.\d{1,3}){3}(?![\d.])', text):
            assert allowed.match(ip), (path.name, ip)


# 체크포인트 A를 해 본 뒤 고친 것(2026-11-16)


def test_a1_asks_for_numbered_comment_lines_under_the_script_limit():
    # 같은 글자 # 20,000개는 Claude 쪽 안전장치가 응답을 멈췄다. 번호 붙은 주석 줄은 지나갔다
    step = _manual_item("A-1")
    assert "`# 줄 0001`" in step
    assert "`#` 20,000자로 채워서" not in step


def test_the_probe_is_removed_from_outside_its_folder():
    finish = ACCEPT.read_text(encoding="utf-8").split("\n### 끝내기", 1)[1].split("\n## ", 1)[0]
    for text in (_manual_item("A-3"), finish):
        assert "`C:\\dev\\hq-mcp-probe` 밖" in text, text


# 체크포인트 B(MCP-1~MCP-11)


def test_checkpoint_b_items_give_steps_and_the_words_to_say_to_claude():
    for n in range(1, 12):
        item = _manual_item(f"MCP-{n}")
        assert re.search(r"^ {2}1\. ", item, flags=re.M), n  # 번호 붙은 단계
        assert "본다" in item or "확인한다" in item, n


def test_checkpoint_b_names_what_the_ledger_left_for_the_user():
    text = _checkpoint_b()
    # 로그인 실패 글에 계정 이름이 드는지(New-PSSession 메시지는 측정하지 않았다)
    assert "계정 이름" in text and "AUTH_FAILED" in text
    # 120초를 넘긴 호출은 백그라운드 작업이 되고 결과는 완료 알림으로 온다(A-5)
    assert "백그라운드" in text and "완료 알림" in text
    # 시작 줄과 끝 줄은 call로 짝짓고, 달이 바뀌면 두 파일에 걸친다
    assert "`call`" in text and "다음 달" in text
    # 금지 IP는 사용자가 Claude에게 알리지 않고 직접 친다
    assert "Claude에게 알리지 않" in _manual_item("MCP-1")


# 스펙에 더한 사실(구현과 검토에서 측정하거나 정한 것)


def test_the_spec_says_type_literals_look_up_assemblies_while_parsing():
    for heading in ("### 7.1 ", "### 7.3 "):
        assert "어셈블리" in _spec_section(heading), heading


def test_the_spec_names_a_path_split_across_an_expansion():
    assert "\"$('\\')\\z\\c$\\x\"" in _spec_section("### 7.3 ")


def test_the_spec_says_which_spellings_of_a_forbidden_ip_pass_the_text_check():
    text = _spec_section("## 6. ")
    for spelling in ("10.0.1.025", "167772437", "0x0a000115", "::ffff:a00:115", "ipv6-literal.net", "5.10.0.1.21"):
        assert spelling in text, spelling


def test_the_spec_says_names_with_hidden_characters_cannot_be_used():
    text = _spec_section("### 4.1 ")
    for category in ("Cc", "Cf", "Zl", "Zp", "Co", "Cn", "Cs"):
        assert category in text, category


def test_the_spec_says_hosts_are_resolved_together_and_a_cancel_before_start_is_rejected():
    text = _spec_section("### 8.1 ")
    assert "함께 풀" in text and "rejected" in text
    assert "스레드를 띄우지 못하면" in text  # 그때도 거절로 남기고 아무것도 실행하지 않는다
    assert "NOT_STARTED" in text  # 사전 확인 중의 취소


def test_the_spec_bounds_the_server_list_and_query_refusals():
    text = _spec_section("## 9. ")
    assert "메모만" in text and "10개" in text and "예외" in text
    assert "비운다" in text and "200자" in text
    assert "200개까지" in _spec_section("### 4.5 ")


def test_the_spec_says_how_two_processes_share_the_logs():
    assert "hq-mcp.log`를 함께 쓴다" in _spec_section("## 3. ")
    text = _spec_section("## 10. ")
    assert "원자적" in text and "`call`" in text
