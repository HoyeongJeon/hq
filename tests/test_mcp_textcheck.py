import time

import pytest

from hq.mcp.textcheck import never_ip_in, parser_hazard


@pytest.mark.parametrize(
    "script",
    [
        "using module \\\\z\\share\\m",
        "using `\nmodule \\\\z\\share\\m",
        "using <# c #> module \\\\z\\share\\m",
        "USING ASSEMBLY C:\\x.dll",
        "using namespace System.IO\nGet-Date",
        "configuration C { Import-DscResource -ModuleName \\\\z\\share\\m }",
    ],
)
def test_scripts_the_parser_would_look_up_are_caught(script):
    assert parser_hazard(script)


@pytest.mark.parametrize(
    "script",
    [
        "Get-Process",
        "$using:cred",
        "# processes using the most memory\nGet-Process",
        "Get-Service | Where-Object { $_.Name -like '*using*' }",
    ],
)
def test_ordinary_scripts_are_not(script):
    assert parser_hazard(script) is None


def test_a_run_of_comments_after_using_is_checked_at_once():
    # 주석 반복이 되돌아가면 주석 하나마다 시간이 두 배로 늘어 40개면 몇 시간이 걸린다. 그동안 서버 전체가 멈춘다
    script = "using " + "<# #>" * 26 + "Get-Date"
    start = time.perf_counter()
    assert parser_hazard(script) is None
    assert time.perf_counter() - start < 1


@pytest.mark.parametrize(
    "script, found",
    [
        ("Test-NetConnection 10.0.1.21 -Port 445", True),
        ("gci '\\\\10.0.1.21\\c$'", True),
        ("x 10.0.1.21:445", True),
        # 이름 속의 주소(nip.io, sslip.io 같은 이름은 그 주소로 풀린다)와 앞에 점이 겹친 주소
        ("app.10.0.1.21.nip.io", True),
        ("..10.0.1.21", True),
        ("x.10.0.1.21", True),
        ("\\\\10.0.1.21\\c$", True),
        ("10.0.1.21:445", True),
        ("http://a.10.0.1.21.sslip.io/x", True),
        # 숫자로 끝나는 이름 조각 뒤의 주소. 그 조각 앞이 영문, _, -이면 이름이다
        ("a1.10.0.1.21.nip.io", True),
        ("web01.10.0.1.21.sslip.io", True),
        ("x.5.10.0.1.21", True),
        ("srv-1.10.0.1.21", True),
        # 빈 조각(..) 뒤의 주소. 그 뒤의 숫자 묶음은 앞의 점 숫자에 이어지지 않는다
        ("5.10.0.1.21..10.0.1.21", True),
        ("x..5.10.0.1.21", True),
        ("Get-Date # 10.0.1.210", False),
        ("110.0.1.21", False),
        ("10.0.1.21.5", False),
        ("1.10.0.1.21", False),
        ("10.0.1.210", False),
        ("5.10.0.1.21", False),
        ("1.2.10.0.1.21", False),
        (" 5.10.0.1.21", False),
        ("(5.10.0.1.21)", False),
    ],
)
def test_a_forbidden_ip_is_found_only_as_a_whole_address(script, found):
    assert (never_ip_in(script, ["10.0.1.21"]) == "10.0.1.21") is found


def test_a_long_run_of_digits_and_dots_is_checked_at_once():
    # 주소가 1,000번 든 20,000자의 점 숫자(공백으로 나눔). 주소마다 앞에 "1.1.1.1.1."이 있어 그 묶음의 앞 글자를
    # 보아야 하고, 모두 더 긴 점 숫자의 꼬리라 끝까지 본다. 자리마다 앞을 처음부터 다시 훑으면 길이의 제곱이 된다
    script = (" " + "1." * 5 + "10.0.1.21") * 1000
    assert len(script) == 20_000
    start = time.perf_counter()
    assert never_ip_in(script, ["10.0.1.21"]) is None
    assert time.perf_counter() - start < 1


def test_a_forbidden_ipv6_is_found_as_text_in_any_case():
    assert never_ip_in("Test-NetConnection FE80::1", ["fe80::1"]) == "fe80::1"


def test_no_forbidden_list_means_nothing_is_found():
    assert never_ip_in("ping 10.0.1.21", []) is None
