"""parse_errors 도우미 자체의 확인. 늘 빈 목록을 돌려주면 다른 문법 검사가 모두 헛것이 된다."""

import pytest
from ps import parse_errors

pytestmark = pytest.mark.integration


def test_valid_script_with_korean_text_has_no_errors(tmp_path):
    script = "# 한글 주석\n$이름 = '웹01 서버'\nWrite-Output \"$이름에 접속 중...\"\n"
    assert parse_errors(script, tmp_path) == []


def test_broken_script_reports_errors(tmp_path):
    assert parse_errors("if ($x {\n    Write-Output '열림'\n", tmp_path) != []
