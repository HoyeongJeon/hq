import sys
from pathlib import Path

import pytest

from hq import paths


def test_data_dir_is_under_localappdata_and_is_created(tmp_path, monkeypatch):
    base = tmp_path / "사용자"
    monkeypatch.setenv("LOCALAPPDATA", str(base))
    result = paths.data_dir()
    assert result == base / "hq"
    assert result.is_dir()


def test_data_dir_needs_localappdata(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(RuntimeError):
        paths.data_dir()


def test_package_dir_in_development_is_the_hq_package():
    assert paths.package_dir() == Path(paths.__file__).resolve().parent


def test_package_dir_inside_pyinstaller_uses_meipass(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.package_dir() == tmp_path / "hq"


def test_resource_is_relative_to_the_package():
    assert paths.resource("ps/bootstrap.ps1") == paths.package_dir() / "ps" / "bootstrap.ps1"


def test_index_url_is_a_file_url_so_pywebview_starts_no_http_server():
    url = paths.index_url()
    assert url.startswith("file:///")
    assert url.endswith("/static/index.html")
