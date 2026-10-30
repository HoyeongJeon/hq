import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE = ROOT / "docs" / "acceptance.md"
SPEC = ROOT / "docs" / "specs" / "2026-10-15-hq-design.md"
# acceptance.md에서 이 PC에서 돌릴 블록을 가리킬 때 쓰는 말. 맨 위에서 HQ 탭과 구별하는 법을 한 번 설명한다
THIS_PC = re.compile(r"이 PC의 PowerShell(?: 창)?\(맨 위 참고\)")
CHECK_ITEM = r"^- \[[ xX]\] (AC-\d+) "  # 사용자가 체크하면 [ ]가 [x]로 바뀌므로 둘 다 받는다


def checklist_ids() -> list[str]:
    text = ACCEPTANCE.read_text(encoding="utf-8")
    return re.findall(CHECK_ITEM, text, flags=re.MULTILINE)


def acceptance_parts() -> tuple[str, dict[str, str]]:
    """(첫 항목 앞의 머리말, AC 번호 → 그 항목부터 다음 항목 앞까지의 글)."""
    text = ACCEPTANCE.read_text(encoding="utf-8")
    intro, *items = re.split(r"(?=^- \[[ xX]\] AC-\d+ )", text, flags=re.MULTILINE)
    return intro, {re.match(CHECK_ITEM, item).group(1): item for item in items}


def spec_section_12() -> str:
    text = SPEC.read_text(encoding="utf-8")
    return text.split("\n## 12. ", 1)[1].split("\n## ", 1)[0]


def spec_rows() -> dict[str, str]:
    return dict(re.findall(r"^\| (AC-\d+) \| (.*) \|$", spec_section_12(), flags=re.MULTILINE))


def spec_ids() -> list[str]:
    return re.findall(r"^\| (AC-\d+) \|", spec_section_12(), flags=re.MULTILINE)


def test_acceptance_lists_each_criterion_once_in_order():
    assert checklist_ids() == [f"AC-{n}" for n in range(1, 12)]


def test_acceptance_covers_every_criterion_in_the_spec():
    ids = spec_ids()
    assert ids  # 12장 표를 읽지 못하면 아래 검사가 저절로 통과하므로 먼저 막는다
    listed = checklist_ids()
    assert [i for i in ids if i not in listed] == []


def test_acceptance_says_up_front_how_to_tell_this_pc_from_an_hq_tab():
    # 체크포인트 D에서 이 PC에서 돌릴 AC-9 블록을 HQ 탭에 붙여 넣어 서버에서 돌았다
    intro, _ = acceptance_parts()
    for part in ("이 PC의 PowerShell", "시작 메뉴", "`PS C:\\...>`", "HQ 탭", "`[서버 주소]:`"):
        assert part in intro, part


def test_acceptance_points_back_to_it_wherever_a_check_runs_on_this_pc():
    _, items = acceptance_parts()
    for ac in ("AC-8", "AC-9", "AC-10"):
        assert THIS_PC.search(items[ac]), ac
    assert len(THIS_PC.findall(items["AC-9"])) >= 3  # 평문 검사, 공용 폴더에 복사, 복사본 지우기
    assert "처음에 쓰던 이 PC의 PowerShell 창" in items["AC-9"]  # 복사본은 다른 계정의 창이 아니라 처음 창에서 지운다
    text = ACCEPTANCE.read_text(encoding="utf-8")
    assert "내 PowerShell" not in text  # 같은 것을 두 이름으로 부르지 않는다
    assert "내 PC의 창" not in text


def test_ac9_tells_which_errors_mean_a_file_hq_keeps_open_by_key_word():
    # 이 PC는 Windows 표시 언어가 영어(en-US)라 체크포인트 D에서 이 오류가 영어로 나왔다. 한국어 표시 언어에서 Windows가
    # 내는 문장은 이 PC에서 확인할 수 없으므로 문장 전체가 아니라 낱말로 맞춘다(D-3 리뷰). 브리프의 한국어 문장은 틀렸다
    _, items = acceptance_parts()
    verdict = next(p for p in items["AC-9"].split("\n\n") if "판정:" in p)
    for word in ("사용 중", "잠갔", "being used by another process", "locked a portion of the file"):
        assert f"`{word}`" in verdict, word
    assert "표시 언어가 영어" in verdict, verdict
    assert "파일의 일부를 잠갔기 때문에" not in ACCEPTANCE.read_text(encoding="utf-8")
    assert re.search(r"HQ를 닫은 뒤[^.]*오류[^.]*없어야", verdict), verdict


def test_ac9_waits_a_few_seconds_after_closing_hq_before_the_second_run():
    # 창을 닫은 뒤에도 WebView2가 잠깐 LOCK 파일을 쥐고 있을 수 있다(D-3 리뷰)
    _, items = acceptance_parts()
    plaintext = next(p for p in items["AC-9"].split("\n\n") if "평문 검사:" in p)
    assert re.search(r"HQ를 닫고 몇 초 기다린 뒤 한 번", plaintext), plaintext


def test_ac5_is_about_the_same_account_as_the_rdp_session():
    # 팀은 Administrator로 RDP하지 않는다. 중요한 것은 hq가 그 RDP 세션과 같은 계정을 쓰는 경우다
    _, items = acceptance_parts()
    for text in (items["AC-5"], spec_rows()["AC-5"]):
        assert "RDP 세션과 같은 계정(예: Administrator)" in text, text
        assert "그 RDP 세션이 끊기지 않는다" in text, text


def test_spec_shows_how_to_write_a_domain_account():
    # validation은 이미 '이름\계정'을 받는다. 스펙도 로컬 계정만 있는 것처럼 적지 않는다
    lines = SPEC.read_text(encoding="utf-8").splitlines()
    for field in ("| 접속 계정 |", "| username |"):
        row = next(line for line in lines if line.startswith(field))
        assert "도메인\\계정" in row and "CONTOSO\\opsadmin" in row, row


def test_spec_says_domain_accounts_already_log_in_over_ntlm():
    # 체크포인트 D: 도메인 계정은 '도메인\계정'으로 적으면 NTLM으로 로그인된다. Kerberos는 여전히 범위 밖이다
    lines = SPEC.read_text(encoding="utf-8").splitlines()
    d2_reason = next(line for line in lines if line.startswith("| D2 |")).split(" | ")[2]
    kerberos = next(line for line in lines if line.startswith("- Kerberos:"))
    for text in (d2_reason, kerberos):
        assert "도메인\\계정" in text and "NTLM" in text, text
    assert "서버 로컬 계정이라" not in d2_reason, d2_reason
    assert "도메인 계정을 쓰게 되면" not in kerberos, kerberos
