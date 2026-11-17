import re
from pathlib import Path

from hq.mcp.runner import STATUS_BY_CODE

ROOT = Path(__file__).resolve().parents[1]
MCP_SPEC = ROOT / "docs/specs/2026-11-04-hq-mcp-design.md"
BASE_SPEC = ROOT / "docs/specs/2026-10-15-hq-design.md"
ACCEPT = ROOT / "docs/mcp-acceptance.md"
HIDE = "계정 이름, 서버 주소와 이름, 컴퓨터 이름, 사용자 폴더 경로는 가린다"


def _checkpoint_b() -> str:
    return ACCEPT.read_text(encoding="utf-8").split("\n## 체크포인트 B", 1)[1]


def _manual_item(item_id: str) -> str:
    """수동 확인 문서에서 그 항목부터 다음 항목이나 다음 절 앞까지의 글."""
    text = ACCEPT.read_text(encoding="utf-8")
    start = re.search(rf"^- \[[ xX]\] {item_id} ", text, flags=re.M).start()
    end = re.compile(r"^(?:- \[[ xX]\] (?:A|MCP)-\d+ |#)", flags=re.M).search(text, start + 1)
    return text[start : end.start() if end else len(text)]


def _spec_section(heading: str, spec: Path = MCP_SPEC) -> str:
    """스펙(기본은 MCP 스펙)에서 heading으로 시작하는 절(다음 같은 깊이 이상의 제목 앞까지)."""
    text = spec.read_text(encoding="utf-8")
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
    text = BASE_SPEC.read_text(encoding="utf-8")
    assert text.count("2026-11-04-hq-mcp-design.md") >= 3
    for heading in ("### 8.1 ", "### 8.3 ", "## 15. "):
        assert "(2026-11-04-hq-mcp-design.md)" in _spec_section(heading, BASE_SPEC), heading


def _addresses(text: str) -> list[str]:
    """글에서 IPv4 주소처럼 보이는 글자들. 문장 끝의 점은 주소의 일부가 아니다.

    건너뛰는 글자는 PowerShell CLIXML의 버전 표시 Version="1.1.0.1" 하나뿐이다(test_mcp_psrun.py).
    """
    text = text.replace('Version="1.1.0.1"', "")
    return re.findall(r"(?<!\d)(?<!\d\.)\d{1,3}(?:\.\d{1,3}){3}(?!\d|\.\d)", text)


def test_the_address_scan_finds_an_address_at_the_end_of_a_sentence_and_skips_only_the_clixml_version():
    assert _addresses("금지할 주소는 203.0.113.7.") == ["203.0.113.7"]
    assert _addresses("(203.0.113.7), 203.0.113.8: x.203.0.113.9") == ["203.0.113.7", "203.0.113.8", "203.0.113.9"]
    assert _addresses('Version="203.0.113.7"') == ["203.0.113.7"]
    # PowerShell CLIXML의 <Objs Version="1.1.0.1">(test_mcp_psrun.py). 주소가 아니다
    assert _addresses('<Objs Version="1.1.0.1" xmlns="x">') == []
    # 더 긴 점 숫자(버전 번호 등)의 일부는 주소로 보지 않는다
    assert _addresses("1.2.3.4.5 10.0.1.210 5.10.0.1.21") == ["10.0.1.210"]


def test_public_files_hold_only_documentation_addresses():
    # 110.0.1.21은 Task 6의 경계 시험 값. 203.0.113.0/24는 192.0.2.0/24, 198.51.100.0/24와 함께 RFC 5737의 문서용
    # 범위다(위의 주소 찾기 시험이 쓴다)
    allowed = re.compile(
        r"^(10\.0\.1\.\d+|192\.0\.2\.\d+|198\.51\.100\.\d+|203\.0\.113\.\d+|127\.0\.0\.1|110\.0\.1\.21)$"
    )
    files = [
        MCP_SPEC,
        ACCEPT,
        ROOT / "README.md",
        BASE_SPEC,
        ROOT / "docs/acceptance.md",
        ROOT / "docs/verification.md",
        ROOT / "docs/plans/2026-11-10-hq-mcp.md",
        *(ROOT / "src/hq/mcp").rglob("*"),
        *(ROOT / "src/hq/ps").glob("mcp-*.ps1"),
        *(ROOT / "tools/mcp").rglob("*"),
        *ROOT.glob("tests/test_mcp_*.py"),
        *ROOT.glob("tests/integration/*mcp*.py"),
    ]
    for path in (f for f in files if f.suffix in {".py", ".json", ".ps1", ".md"}):
        for ip in _addresses(path.read_text(encoding="utf-8", errors="replace")):
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


def test_checkpoint_b_items_give_steps_and_what_must_be_seen():
    for n in range(1, 12):
        item = _manual_item(f"MCP-{n}")
        assert re.search(r"^ {2}1\. ", item, flags=re.M), n  # 번호 붙은 단계
        assert "보여야 하는 것:" in item, n
        assert len(item.splitlines()) < 60, n  # 시간이 없는 사람이 한 번에 읽는다


def test_the_document_says_to_hide_private_details_in_what_is_written_down():
    # 공개 저장소다. 결과를 적을 때 계정, 서버, 컴퓨터, 사용자 폴더가 드러나지 않게 한다
    intro = ACCEPT.read_text(encoding="utf-8").split("\n## ", 1)[0]
    assert HIDE in intro
    assert _manual_item("MCP-7").count(HIDE) >= 2


def test_the_preparation_turns_on_enough_servers_and_says_how_a_forbidden_name_shows():
    prep = _checkpoint_b().split("\n### 준비", 1)[1].split("\n### ", 1)[0]
    for word in ("2012 R2", "2019", "MCP-7", "MCP-1", "이름으로 등록한", "켜지 않는다"):
        assert word in prep, word


def test_mcp1_counts_a_baseline_and_states_the_pass_rule():
    item = _manual_item("MCP-1")
    # 카운터는 이 PC에서 필터를 따르지 않았다(2026-11-17). 파일로 받아 주소를 찾고, 켠 서버로 방법을 확인한다
    for word in ("pktmon etl2txt", "`패킷 필터`로 시작하는 필터 기록 줄뿐이면 통과", "켠 서버 줄 수", "0이면 통과로 치지 않는다"):
        assert word in item, word
    assert "pktmon start -c -o" not in item


def test_every_script_is_read_in_the_window_before_it_is_allowed():
    allowing = [line for line in _checkpoint_b().splitlines() if "허용한다" in line]
    assert allowing
    for line in allowing:
        assert "보고 허용" in line, line


def test_mcp3_counts_start_lines_by_reason():
    item = _manual_item("MCP-3")
    assert "Measure-Object" in item and "$_.reason -like" in item


def test_mcp7_never_changes_the_stored_password():
    item = _manual_item("MCP-7")
    assert "비밀번호는 바꾸지 않는다" in item and "`hq-mcp-nouser`" in item
    assert "틀린 값" not in item


def test_mcp8_reads_the_end_line_of_the_stopped_call():
    item = _manual_item("MCP-8")
    assert "$_.reason -like '*취소 시험*'" in item and "아직 도는 중" in item
    assert "취소 시험의 start 줄이 없다" in item and "10분이 지나도" in item


def test_checkpoint_b_keeps_away_from_the_forbidden_server_and_the_stored_password():
    text = _checkpoint_b()
    # HQ 창은 금지 목록을 따르지 않고 목록의 모든 서버 포트를 확인한다
    assert "HQ 창은 금지 목록을 따르지 않는다" in text.split("\n### 준비", 1)[1].split("\n### ", 1)[0]
    for item_id in ("MCP-7", "MCP-9"):
        assert "준비의 경고" in _manual_item(item_id), item_id
    mcp9 = _manual_item("MCP-9")
    assert "금지 서버는 열지 않는다" in mcp9 and "AC-3은 `<대상>`을 더블클릭한다" in mcp9
    # 비밀번호 창에 치면 저장된 비밀번호를 덮어쓴다. ID는 메모장에 적어 두고 되돌린다
    mcp7 = _manual_item("MCP-7")
    assert "`ID`" in mcp7 and "메모장" in mcp7 and "아무것도 치지 말고 취소한다" in mcp7
    # 실행 기록은 모든 달 파일에서 이유(reason)로 찾고, 찾지 못하면 그렇게 말한다
    assert "Get-Date -Format yyyy-MM" not in text
    assert "실행 기록이 아직 없다" in text
    mcp3 = _manual_item("MCP-3")
    assert "-like '*확인 창 시험*'" in mcp3 and "-like '*긴 스크립트 시험*'" in mcp3 and "[Math]::Max(0" in mcp3
    # 카운터로 세지 않는다. 필터를 걸어도 모든 패킷을 셌다
    mcp1 = _manual_item("MCP-1")
    assert "`pktmon counters`로 세는 방법은 쓰지 않는다" in mcp1
    assert "Start-Sleep -Seconds 100" in _manual_item("MCP-6")
    assert "한 글자 `\\`로 읽는다" in _manual_item("MCP-4")
    assert _manual_item("MCP-11").count("보여야 하는 것:") >= 7  # 1번, 3번의 블록 다섯, 4번


def test_mcp11_exits_at_once_if_bypass_turns_on_and_removes_its_files():
    item = _manual_item("MCP-11")
    assert "바로 `/exit`" in item and "C:\\dev\\hq-ops\\check.txt" in item.split("\n  4. ", 1)[1]


def test_the_readme_says_only_what_the_mcp_server_does():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for words in (
        "does not connect to an address you forbid",
        "DNS",
        "HQ's own window",
        "recorded in full before it runs",
        "nothing runs",
        "are not recorded",
        "can change the query check",
    ):
        assert words in text, words
    assert "is never contacted from this PC" not in text


def test_the_spec_says_what_the_record_holds_and_what_it_leaves_out():
    text = _spec_section("## 10. ")
    assert "시작 줄을 쓰지 못하면" in text and "설정 파일이나 서버 목록" in text


def test_public_documents_hold_no_real_profile_folder():
    # 사람의 계정 이름이 든 사용자 폴더 경로를 적지 않는다. <사용자> 같은 자리 표시와 Windows가 만드는 Public,
    # Administrator(서버 프롬프트의 C:\Users\Administrator\Documents)는 된다
    for path in (ROOT / "README.md", ROOT / "docs/specs/2026-10-15-hq-design.md", MCP_SPEC, ACCEPT,
                 ROOT / "docs/acceptance.md", ROOT / "docs/verification.md"):  # fmt: skip
        text = path.read_text(encoding="utf-8")
        found = re.findall(r"C:[\\/]Users[\\/](?!<|(?:Public|Administrator)[\\/])[^\\/\s`)]+", text)
        assert found == [], path.name


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


def test_the_spec_says_which_characters_a_script_and_a_reason_cannot_hold():
    script = _spec_section("### 4.5 ")
    assert "홀로 쓴 CR" in script and "`U+001B`" in script and "`[char]0x1B`" in script
    assert "U+0020" in _spec_section("### 4.4 ") and "유일한 장치" in _spec_section("### 4.4 ")
    assert "홀로 쓴 CR" in _spec_section("## 12. ")


def test_the_spec_bounds_the_wrong_target_refusal():
    text = _spec_section("## 9. ")
    assert "3개까지" in text and "80자" in text and "20개까지" in text and "list_servers를 다시 부르세요" in text


def test_the_spec_says_how_two_processes_share_the_logs():
    assert "hq-mcp.log`를 함께 쓴다" in _spec_section("## 3. ")
    text = _spec_section("## 10. ")
    assert "원자적" in text and "`call`" in text


# 체크포인트 B 전의 마지막 검토에서 고친 것


def test_checkpoint_b_skips_what_opens_the_hq_window_when_the_forbidden_server_is_listed():
    # HQ 창은 열거나 저장할 때 목록의 모든 서버의 5985 포트를 확인한다(금지 서버 포함)
    prep = _checkpoint_b().split("\n### 준비", 1)[1].split("\n### ", 1)[0]
    skipped = "`금지 서버가 목록에 있어 건너뜀`"
    assert skipped in prep and "MCP-7의 2~4번과 MCP-9를 하지 않는다" in prep
    for item_id in ("MCP-7", "MCP-9"):
        assert skipped in _manual_item(item_id), item_id


def test_checkpoint_b_turns_servers_on_by_host_and_keeps_the_repo_and_the_limit_still():
    prep = _checkpoint_b().split("\n### 준비", 1)[1].split("\n### ", 1)[0]
    assert "하나씩" in prep and "`on --group`은 이름으로 등록한 서버도" in prep
    assert "`C:\\dev\\hq`의 파일을 고치지 않고" in prep and "개발 세션" in prep
    assert "세션을 하나만" in prep and "16개" in prep


def test_the_docs_say_rules_and_templates_are_read_on_every_call():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    spec = _spec_section("### 13.1 ")
    for name in ("query_rules.json", "mcp-check.ps1", "mcp-run.ps1", "bootstrap.ps1"):
        assert name in readme and name in spec, name
    assert "already running" in readme and "이미 떠 있는 세션" in spec


def test_the_spec_lists_every_status_a_result_can_have():
    line = next(line for line in _spec_section("## 9. ").splitlines() if line.startswith("- `status`:"))
    # runner의 종료 코드 표 밖의 상태: 띄우지 못함, 알 수 없음(runner), 풀지 못함, 시작하지 않음(tools)
    expected = {*STATUS_BY_CODE.values(), "SPAWN_FAILED", "UNKNOWN", "UNRESOLVED", "NOT_STARTED"}
    assert sorted(re.findall(r"`([A-Z_]+)`", line)) == sorted(expected)


def test_the_spec_says_what_the_limits_and_the_check_cover():
    assert "프로세스 하나" in _spec_section("### 8.1 ") and "16개" in _spec_section("### 8.1 ")
    arguments = _spec_section("### 4.5 ")
    assert "주석" in arguments and "15초 이하" in arguments
    limits = _spec_section("### 7.3 ")
    for words in (
        "PowerShell 5.1",
        "4.0",
        "Get-AuthenticodeSignature",
        "1 | Select-Object @{n='PSPath';e={'\\\\z\\c$\\x'}} | Get-Content",
        "Win32_SoftwareFeature",
    ):
        assert words in limits, words
    assert "MCP 의존성 추가" not in _spec_section("## 14. ")
    log = _spec_section("## 3. ")
    assert "줄을 잃을 수 있다" in log and "실행 기록(10장)" in log
