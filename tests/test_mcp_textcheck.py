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
        ("Get-Date # 10.0.1.210", False),
        ("110.0.1.21", False),
        ("10.0.1.21.5", False),
        ("1.10.0.1.21", False),
        ("10.0.1.210", False),
        ("5.10.0.1.21", False),
        ("1.2.10.0.1.21", False),
    ],
)
def test_a_forbidden_ip_is_found_only_as_a_whole_address(script, found):
    assert (never_ip_in(script, ["10.0.1.21"]) == "10.0.1.21") is found


def test_a_forbidden_ipv6_is_found_as_text_in_any_case():
    assert never_ip_in("Test-NetConnection FE80::1", ["fe80::1"]) == "fe80::1"


def test_no_forbidden_list_means_nothing_is_found():
    assert never_ip_in("ping 10.0.1.21", []) is None
