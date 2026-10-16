import pytest

from hq.paths import resource
from hq.server_setup import build_server_setup_script
from hq.validation import ValidationError

# PowerShell 2.0(Windows Server 2008 R2)에 없는 문법·명령
PS2_FORBIDDEN = ["[pscustomobject]", "[ordered]", "::new(", ".ForEach(", ".Where(", " -in ", " -notin ", "$PSItem",
                 "ConvertFrom-Json", "ConvertTo-Json", "Get-CimInstance", "-SkipNetworkProfileCheck",
                 "Set-NetFirewallRule", "Get-NetFirewallRule", "Get-LocalUser"]


def test_fills_account_and_allowed_ips():
    script = build_server_setup_script("opsadmin", ["10.0.0.50", "10.0.0.51"])
    assert "$Account = 'opsadmin'" in script
    assert "$AllowFrom = @('10.0.0.50','10.0.0.51')" in script
    assert "__HQ_" not in script


def test_no_restriction_means_an_empty_list():
    assert "$AllowFrom = @()" in build_server_setup_script(".\\Administrator")


def test_rejects_bad_values():
    with pytest.raises(ValidationError):
        build_server_setup_script("admin'; calc")
    with pytest.raises(ValidationError):
        build_server_setup_script("opsadmin", ["10.0.0"])


def test_template_placeholders_appear_once():
    text = resource("ps/server-setup.ps1").read_text(encoding="utf-8-sig")
    assert text.count("__HQ_ACCOUNT__") == 1
    assert text.count("__HQ_ALLOW_FROM__") == 1


@pytest.mark.parametrize("token", PS2_FORBIDDEN)
def test_script_avoids_powershell_3_only_features(token):
    assert token.lower() not in build_server_setup_script("opsadmin").lower()


def test_ps2_check_reads_the_winrm_restart_lines():
    # 규칙 P12로 더한 WinRM 다시 시작도 템플릿 안에 있어서 위 PowerShell 2.0 검사를 함께 받는다.
    assert "Restart-Service -Name WinRM" in build_server_setup_script("opsadmin")


def test_account_that_looks_like_a_placeholder_stays_as_typed():
    # 밑줄은 ID에 쓸 수 있는 문자다. 넣은 값이 다른 자리표시자로 다시 바뀌면 따옴표가 깨진다.
    script = build_server_setup_script("__HQ_ALLOW_FROM__", ["10.0.0.50"])
    assert "$Account = '__HQ_ALLOW_FROM__'" in script
    assert "$AllowFrom = @('10.0.0.50')" in script


def test_script_has_no_base64_decoding():
    # AhnLab V3는 FromBase64String이 든 PowerShell 명령을 막는다. 값은 검사한 뒤 작은따옴표로만 넣는다.
    assert "frombase64string" not in build_server_setup_script("opsadmin", ["10.0.0.50"]).lower()
