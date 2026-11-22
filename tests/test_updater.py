"""3.4: the self-update checker — a manifest tells every copy of the app when something newer is out."""
import hashlib
import os
from pathlib import Path

os.environ["LAPTOP_AI_HOME"] = os.path.join(os.path.dirname(__file__), "_home")

import pytest  # noqa: E402

from laptop_ai import config, updater  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "app")
    return tmp_path / "app"


class FakeResponse:
    def __init__(self, status=200, data=None, chunks=None, headers=None):
        self.status_code = status
        self._data = data
        self._chunks = chunks or []
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._data

    def iter_content(self, chunk_size=1 << 16):
        return iter(self._chunks)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_a_plain_version_compare_knows_which_is_newer():
    assert updater.is_newer("3.3.0", "3.2.9")
    assert updater.is_newer("3.10.0", "3.9.0")
    assert not updater.is_newer("3.2.0", "3.3.0")
    assert not updater.is_newer("3.3.0", "3.3.0")


def test_no_manifest_url_set_gives_a_plain_message():
    info, message = updater.check("")
    assert info is None and "update link has been set up" in message


def test_checking_when_already_current_finds_nothing(monkeypatch):
    monkeypatch.setattr(updater.requests, "get", lambda *a, **k: FakeResponse(data={"version": "0.1.0", "installer_url": "x"}))
    info, message = updater.check("https://example.test/update.json")
    assert info is None and "newest version" in message


def test_checking_finds_a_newer_version(monkeypatch):
    monkeypatch.setattr(updater.requests, "get",
                        lambda *a, **k: FakeResponse(data={"version": "99.0.0", "notes": "fixes", "installer_url": "https://x/setup.exe", "sha256": "abc"}))
    info, message = updater.check("https://example.test/update.json")
    assert info is not None and info.version == "99.0.0" and "available" in message


def test_a_broken_or_unreachable_manifest_never_looks_like_up_to_date(monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("no net")
    monkeypatch.setattr(updater.requests, "get", boom)
    info, message = updater.check("https://example.test/update.json")
    assert info is None and "Couldn't check" in message


def test_download_verifies_the_hash_and_rejects_a_bad_one(home, monkeypatch, tmp_path):
    data = b"this is the installer bytes"
    good_hash = hashlib.sha256(data).hexdigest()
    info = updater.UpdateInfo(version="9.0.0", notes="", installer_url="https://x/setup.exe", sha256=good_hash)

    class Session:
        def get(self, *a, **k):
            return FakeResponse(chunks=[data], headers={"Content-Length": str(len(data))})
    ok, message, path = updater.download(info, session=Session())
    assert ok and path is not None and path.read_bytes() == data

    bad = updater.UpdateInfo(version="9.0.0", notes="", installer_url="https://x/setup.exe", sha256="0" * 64)
    ok2, message2, path2 = updater.download(bad, session=Session())
    assert not ok2 and path2 is None and "didn't match" in message2


def test_download_can_be_cancelled_partway(home):
    info = updater.UpdateInfo(version="9.0.0", notes="", installer_url="https://x/setup.exe", sha256="")

    class Session:
        def get(self, *a, **k):
            return FakeResponse(chunks=[b"a", b"b", b"c"], headers={})
    ok, message, path = updater.download(info, cancelled=lambda: True, session=Session())
    assert not ok and path is None and "stopped" in message


def test_apply_and_restart_launches_the_installer_and_signals_exit(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    exited = []
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"x")
    ok = updater.apply_and_restart(installer, before_exit=lambda: exited.append(True))
    assert ok and calls and exited == [True]
