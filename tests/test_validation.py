import pytest

from hq.validation import ValidationError, is_hex_id, validate_host, validate_ipv4, validate_username


@pytest.mark.parametrize("value", ["10.0.1.21", "192.168.0.1", "web01", "web-01.corp.local", "  10.0.1.21  "])
def test_valid_hosts(value):
    assert validate_host(value) == value.strip()


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "999.1.1.1",
        "10.0.1",
        "10.0.1.21; Remove-Item C:\\",
        "$(calc)",
        "a b",
        "web_01",
        "-web",
        "web'01",
        'web"01',
        "web`01",
        "a\nb",
        "x" * 254,
        "서버01",
    ],
)
def test_invalid_hosts(value):
    with pytest.raises(ValidationError):
        validate_host(value)


@pytest.mark.parametrize(
    "value", ["Administrator", "opsadmin", ".\\Administrator", "WEB01\\Administrator", "gw.admin-1_x"]
)
def test_valid_usernames(value):
    assert validate_username(value) == value


@pytest.mark.parametrize(
    "value",
    ["", "admin'", 'a"b', "a`b", "$x", "a b", "a\\b\\c", "\\admin", "admin\\", "x" * 65, "관리자", "a;b"],
)
def test_invalid_usernames(value):
    with pytest.raises(ValidationError):
        validate_username(value)


def test_valid_ipv4():
    assert validate_ipv4(" 10.0.0.50 ") == "10.0.0.50"


@pytest.mark.parametrize("value", ["", "10.0.0", "host", "10.0.0.256"])
def test_invalid_ipv4(value):
    with pytest.raises(ValidationError):
        validate_ipv4(value)


def test_validation_error_is_a_value_error_with_korean_message():
    with pytest.raises(ValueError, match="IP/호스트"):
        validate_host("")


def test_is_hex_id_accepts_32_lowercase_hex():
    assert is_hex_id("0123456789abcdef0123456789abcdef")


@pytest.mark.parametrize(
    "value",
    [
        "0123456789ABCDEF0123456789ABCDEF",
        "0123456789abcdef0123456789abcde",
        "0123456789abcdef0123456789abcdef0",
        "0123456789abcdef0123456789abcdef\n",
        "",
        None,
        12345678901234567890123456789012,
    ],
)
def test_is_hex_id_rejects_everything_else(value):
    assert not is_hex_id(value)
