import os
from pathlib import Path

import pytest

from hq.fileops import read_text_with_retry, replace_with_retry


def test_replace_tries_again_after_a_permission_error(tmp_path, monkeypatch):
    calls = []
    real = os.replace

    def flaky(src, dst):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(5, "Access is denied")
        real(src, dst)

    monkeypatch.setattr("hq.fileops.os.replace", flaky)
    (tmp_path / "a").write_text("new")
    (tmp_path / "b").write_text("old")
    replace_with_retry(tmp_path / "a", tmp_path / "b", delay=0)
    assert (tmp_path / "b").read_text() == "new" and len(calls) == 3


def test_replace_gives_up_with_the_same_error(tmp_path, monkeypatch):
    errors, waits = [], []

    def locked(src, dst):
        errors.append(PermissionError(5, "Access is denied"))
        raise errors[-1]

    monkeypatch.setattr("hq.fileops.os.replace", locked)
    monkeypatch.setattr("hq.fileops.time.sleep", waits.append)
    with pytest.raises(PermissionError) as caught:
        replace_with_retry(tmp_path / "a", tmp_path / "b")
    assert len(errors) == 5 and caught.value is errors[-1]
    assert waits == [0.05] * 4  # 시도 사이에만 기다린다. 마지막 실패 뒤에는 기다리지 않고 바로 던진다


def test_replace_fails_at_once_when_the_source_is_missing(tmp_path, monkeypatch):
    calls = []
    real = os.replace

    def counted(src, dst):
        calls.append(1)
        real(src, dst)

    monkeypatch.setattr("hq.fileops.os.replace", counted)
    with pytest.raises(FileNotFoundError):
        replace_with_retry(tmp_path / "missing", tmp_path / "b", delay=0)
    assert len(calls) == 1


def test_read_tries_again_but_a_missing_file_fails_at_once(tmp_path, monkeypatch):
    calls = []
    real = Path.read_text

    def flaky(self, *args, **kwargs):
        calls.append(self.name)
        if self.name == "busy" and len(calls) < 3:
            raise PermissionError(5, "Access is denied")
        return real(self, *args, **kwargs)

    (tmp_path / "busy").write_text("서버 목록", encoding="utf-8")
    monkeypatch.setattr("hq.fileops.Path.read_text", flaky)
    assert read_text_with_retry(tmp_path / "busy", delay=0) == "서버 목록"
    assert calls == ["busy"] * 3
    calls.clear()
    with pytest.raises(FileNotFoundError):
        read_text_with_retry(tmp_path / "missing", delay=0)
    assert calls == ["missing"]
