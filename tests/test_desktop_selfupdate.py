"""Self-update: streamed installer download with progress + exact silent command."""
from pathlib import Path

import pytest

from desktop import selfupdate


class _FakeStream:
    def __init__(self, chunks: list[bytes], status: int = 200, total: int | None = None):
        self._chunks, self._status = chunks, status
        size = total if total is not None else sum(len(c) for c in chunks)
        self.headers = {"Content-Length": str(size)} if size else {}

    def raise_for_status(self):
        if self._status != 200:
            raise RuntimeError(f"HTTP {self._status}")

    def iter_content(self, chunk_size):
        yield from self._chunks


def test_download_streams_to_plain_versioned_name_with_progress(tmp_path: Path):
    seen, urls = [], []

    def fake_get(url, stream=None, timeout=None):
        urls.append(url)
        return _FakeStream([b"aa", b"bb", b"c"])

    dest = selfupdate.download_installer("https://backlogquest.xyz/", "0.1.5",
                                         dest_dir=tmp_path, progress=lambda d, t: seen.append((d, t)),
                                         get=fake_get)
    assert urls == ["https://backlogquest.xyz/download/scraper/payload"]
    assert dest == tmp_path / "BacklogQuest-Scraper-Setup-0.1.5.exe"
    assert dest.read_bytes() == b"aabbc"
    assert seen == [(2, 5), (4, 5), (5, 5)]
    assert ".h-" not in dest.name and ".c-" not in dest.name   # plain: sidecar untouched


def test_download_overwrites_previous_attempt(tmp_path: Path):
    (tmp_path / "BacklogQuest-Scraper-Setup-0.1.5.exe").write_bytes(b"old-half-download")
    dest = selfupdate.download_installer("https://s", "0.1.5", dest_dir=tmp_path,
                                         get=lambda url, stream=None, timeout=None: _FakeStream([b"new"]))
    assert dest.read_bytes() == b"new"


def test_download_http_error_raises_and_leaves_no_file(tmp_path: Path):
    with pytest.raises(RuntimeError, match="HTTP 404"):
        selfupdate.download_installer("https://s", "0.1.5", dest_dir=tmp_path,
                                      get=lambda url, stream=None, timeout=None: _FakeStream([], status=404))
    assert list(tmp_path.iterdir()) == []


def test_installer_command_exact_flags(tmp_path: Path):
    exe = tmp_path / "setup.exe"
    assert selfupdate.installer_command(exe) == [
        str(exe), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCHAPP=1"]


def test_launch_installer_spawns_detached(tmp_path: Path):
    calls = {}

    def fake_spawn(cmd, **kwargs):
        calls["cmd"], calls["kwargs"] = cmd, kwargs

    selfupdate.launch_installer(tmp_path / "setup.exe", spawn=fake_spawn)
    assert calls["cmd"] == selfupdate.installer_command(tmp_path / "setup.exe")
    assert calls["kwargs"]["close_fds"] is True
