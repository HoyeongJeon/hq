import os
from pathlib import Path

import pytest

from hq.secret_store import SecretStore, _unprotect
from hq.validation import ValidationError

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


def test_save_puts_the_whole_temp_file_on_disk_before_replacing(tmp_path, monkeypatch):
    # 정전이 저장 바로 뒤에 나도 빈 .dpapi 파일(탭이 '비밀번호 읽기 실패'로 끝남)이 남지 않게 한다(M-6)
    calls = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd):
        calls.append(("fsync", os.fstat(fd).st_size))
        real_fsync(fd)

    def replace(src, dst):
        calls.append(("replace", Path(src).name, Path(dst).name))
        real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    store = SecretStore(tmp_path)
    store.save(SERVER_ID, "x")
    size = store.path_for(SERVER_ID).stat().st_size
    assert size > 0
    assert calls == [("fsync", size), ("replace", f"{SERVER_ID}.tmp", f"{SERVER_ID}.dpapi")]


def test_a_failed_save_leaves_no_temp_file_and_keeps_the_old_secret(tmp_path, monkeypatch):
    store = SecretStore(tmp_path)
    store.save(SERVER_ID, "old")
    before = store.path_for(SERVER_ID).read_text(encoding="ascii")

    def locked(src, dst):
        raise PermissionError(5, "액세스가 거부되었습니다")

    monkeypatch.setattr(os, "replace", locked)
    with pytest.raises(PermissionError):
        store.save(SERVER_ID, "new")
    assert [p.name for p in tmp_path.iterdir()] == [f"{SERVER_ID}.dpapi"]
    assert store.path_for(SERVER_ID).read_text(encoding="ascii") == before


def test_a_password_with_a_lone_surrogate_is_refused_in_korean_and_never_echoed(tmp_path):
    # 화면(JS 문자열)에서는 짝 없는 UTF-16 서로게이트가 올 수 있다. 영어 UnicodeEncodeError 대신 한국어로 알린다(I-1)
    store = SecretStore(tmp_path)
    with pytest.raises(ValidationError, match=r"[가-힣]") as caught:
        store.save(SERVER_ID, "Secret\ud800pw")
    assert "Secret" not in str(caught.value)
    # 원래 예외(비밀번호 전체를 .object로 쥔 UnicodeEncodeError)와 잇지 않는다
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert list(tmp_path.iterdir()) == []
