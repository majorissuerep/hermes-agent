"""Tar is RAM-only transport; shared browser policy remains the durable archive policy.

Pure filesystem stand-ins: capacity is labelled, no target/browser/application runs here.
"""
import io
import stat
import tarfile

import pytest

from hermes_security import browser_profile_transport as transport
from hermes_security.tree_archive import ArchiveError


@pytest.fixture
def capacity(monkeypatch):
    from hermes_platform.memory_capacity import CapacityBudget
    monkeypatch.setattr(transport, "capacity_budget", lambda _path: CapacityBudget(512 * 1024**2, "unit capacity seam"))


def test_transfer_preserves_site_data_and_shared_exclusions(capacity, tmp_path):
    source, restored = tmp_path / "source", tmp_path / "restored"
    source.mkdir(mode=0o700)
    restored.mkdir(mode=0o700)
    files = {"Default/Cookies": b"synthetic-cookie", "Default/Service Worker/CacheStorage/store": b"offline-only",
             "unknownCache/tmp-state": b"retained-private-state", "Default/Cache/drop": b"regenerable"}
    for relative, data in files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    encoded = io.BytesIO()
    expected = transport.pack(source, encoded)
    encoded.seek(0)
    assert transport.unpack(encoded, restored) == expected
    assert not (restored / "Default/Cache").exists()
    assert (restored / "Default/Cookies").read_bytes() == files["Default/Cookies"]
    assert (restored / "Default/Service Worker/CacheStorage/store").read_bytes() == b"offline-only"
    assert (restored / "unknownCache/tmp-state").read_bytes() == b"retained-private-state"
    assert stat.S_IMODE((restored / "Default/Cookies").stat().st_mode) == 0o600
    assert (source / "Default/Cache/drop").read_bytes() == b"regenerable"


@pytest.mark.parametrize("unsafe", ["traversal", "symlink", "hardlink", "duplicate", "fifo", "oversize", "file-parent"])
def test_invalid_transfer_is_rejected_before_any_member_is_used(capacity, tmp_path, monkeypatch, unsafe):
    encoded = io.BytesIO()
    with tarfile.open(fileobj=encoded, mode="w") as archive:
        safe = tarfile.TarInfo("Cookies")
        safe.size = 4
        archive.addfile(safe, io.BytesIO(b"safe"))
        names = {"traversal": "../escape", "duplicate": "Cookies", "file-parent": "Cookies/child"}
        bad = tarfile.TarInfo(names.get(unsafe, "unexpected"))
        bad.type = {"symlink": tarfile.SYMTYPE, "hardlink": tarfile.LNKTYPE, "fifo": tarfile.FIFOTYPE}.get(unsafe, bad.type)
        if unsafe in {"symlink", "hardlink"}:
            bad.linkname = "Cookies"
        if unsafe == "oversize":
            monkeypatch.setattr(transport.profiles, "MAX_PROFILE_BYTES", 1)
        archive.addfile(bad)
    target = tmp_path / "fresh"
    target.mkdir(mode=0o700)
    encoded.seek(0)
    with pytest.raises(ArchiveError):
        transport.unpack(encoded, target)
    assert list(target.iterdir()) == []
