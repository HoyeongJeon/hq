"""ps.py 도우미 자체의 확인. parse_errors가 늘 빈 목록을 돌려주면 다른 문법 검사가 모두 헛것이 된다."""

import ctypes

import pytest
from ps import parse_errors, run_ps

pytestmark = pytest.mark.integration


def test_valid_script_with_korean_text_has_no_errors(tmp_path):
    script = "# 한글 주석\n$이름 = '웹01 서버'\nWrite-Output \"$이름에 접속 중...\"\n"
    assert parse_errors(script, tmp_path) == []


def test_broken_script_reports_errors(tmp_path):
    assert parse_errors("if ($x {\n    Write-Output '열림'\n", tmp_path) != []


def test_run_ps_leaves_the_callers_console_title_alone():
    # 부트스트랩의 준비 신호는 콘솔 제목을 바꾼다. run_ps가 pytest의 콘솔을 함께 쓰면 테스트를 돌린 터미널의
    # 제목이 바뀐다.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    title = ctypes.create_unicode_buffer(1024)
    if not kernel32.GetConsoleTitleW(title, len(title)):
        pytest.skip("이 pytest에는 제목이 있는 콘솔이 없다")
    before = title.value
    try:
        run_ps("$Host.UI.RawUI.WindowTitle = 'hq-title' + '-probe'")
        kernel32.GetConsoleTitleW(title, len(title))
        assert title.value == before
    finally:
        kernel32.SetConsoleTitleW(before)
