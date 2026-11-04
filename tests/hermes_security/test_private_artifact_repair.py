"""Legacy private outputs remain visible to repair without encrypting public tool caches."""
from __future__ import annotations

import pytest

from hermes_security import frames, io, vault
from hermes_security.migrate import repair_clobbered_state, scan_for_plaintext


@pytest.fixture
def legacy_home(tmp_path, monkeypatch):
    home = tmp_path / "owner"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(home, "synthetic legacy artifact credential")
    vault.unlock(home, "synthetic legacy artifact credential")
    payloads = {
        "cache/terminal-output/out-old.log": b"synthetic private terminal 2b9c\nlast line",
        "cache/spillover/old.txt": b"synthetic private tool result 77ac",
        "cache/screenshots/old.png": b"\x89PNG\r\n\x1a\nsynthetic private screenshot 543e",
        "browser_screenshots/old.png": b"\x89PNG\r\n\x1a\nsynthetic older capture 31bf",
    }
    for relative, data in payloads.items():
        path = home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    public = home / "cache" / "uv" / "public-wheel.whl"
    public.parent.mkdir(parents=True)
    public.write_bytes(b"synthetic public package archive")
    yield home, payloads, public
    vault.clear_vault_cache()


def test_readonly_scan_reports_private_cache_and_preserves_public_code(legacy_home):
    home, payloads, public = legacy_home
    before = {name: (home / name).read_bytes() for name in payloads}
    assert set(scan_for_plaintext(home)) == set(payloads)
    assert {name: (home / name).read_bytes() for name in payloads} == before
    assert public.read_bytes() == b"synthetic public package archive"


def test_repair_seals_legacy_outputs_and_keeps_authenticated_readers(legacy_home):
    from hermes_security import artifacts
    from tools.browser_media_custody import read_owned_media

    home, payloads, public = legacy_home
    result = repair_clobbered_state(home)
    assert result["unrecoverable"] == []
    for relative, data in payloads.items():
        path = home / relative
        assert data not in path.read_bytes(), relative
        recovered = (b"".join(frames.read_frames(path, purpose="log"))
                     if path.suffix == ".log" else io.read_bytes(path, purpose="state"))
        assert recovered == data
        assert artifacts.read_bytes(path, home=home) == data
        if path.suffix == ".png":
            assert read_owned_media(path) == data
    assert scan_for_plaintext(home) == []
    assert public.read_bytes() == b"synthetic public package archive"
    vault.lock_now(home)
    assert set(scan_for_plaintext(home)) == set(payloads)
