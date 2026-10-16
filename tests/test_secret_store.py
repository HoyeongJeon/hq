import pytest

from hq.secret_store import SecretStore, _unprotect

SERVER_ID = "0123456789abcdef0123456789abcdef"
TRICKY = "P@ss 한글'\"$x`;\n끝"


def test_save_writes_only_hex_that_decrypts_back(tmp_path):
    store = SecretStore(tmp_path / "secrets")
    store.save(SERVER_ID, TRICKY)
    text = store.path_for(SERVER_ID).read_text(encoding="ascii")
    assert text and all(c in "0123456789abcdef" for c in text)
    assert _unprotect(bytes.fromhex(text)).decode("utf-16-le") == TRICKY


def test_file_never_contains_the_password(tmp_path):
    store = SecretStore(tmp_path)
    store.save(SERVER_ID, "Secret-12345")
    raw = store.path_for(SERVER_ID).read_bytes()
    for encoding in ("utf-8", "utf-16-le", "ascii"):
        assert "Secret-12345".encode(encoding) not in raw


def test_has_save_overwrite_and_delete(tmp_path):
    store = SecretStore(tmp_path)
    assert not store.has(SERVER_ID)
    store.save(SERVER_ID, "first")
    store.save(SERVER_ID, "second")
    assert store.has(SERVER_ID)
    text = store.path_for(SERVER_ID).read_text(encoding="ascii")
    assert _unprotect(bytes.fromhex(text)).decode("utf-16-le") == "second"
    store.delete(SERVER_ID)
    assert not store.has(SERVER_ID)
    store.delete(SERVER_ID)  # 없어도 오류가 아니다


@pytest.mark.parametrize("bad", ["../escape", "ABCDEF0123456789ABCDEF0123456789", "123", ""])
def test_rejects_ids_that_are_not_32_hex(tmp_path, bad):
    with pytest.raises(ValueError):
        SecretStore(tmp_path).path_for(bad)


def test_rejects_empty_password(tmp_path):
    with pytest.raises(ValueError):
        SecretStore(tmp_path).save(SERVER_ID, "")


def test_no_temp_file_left_behind(tmp_path):
    store = SecretStore(tmp_path)
    store.save(SERVER_ID, "x")
    assert [p.name for p in tmp_path.iterdir()] == [f"{SERVER_ID}.dpapi"]
