"""Legacy private outputs remain visible to repair without encrypting public tool caches."""
from __future__ import annotations

import pytest
from pathlib import Path

from hermes_security import artifacts, frames, io, vault
from hermes_security.artifact_paths import terminal_frame_purpose
from hermes_security.errors import VaultError
from hermes_security.migrate import migrate_home, repair_clobbered_state, scan_for_plaintext


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
        recovered = (b"".join(frames.read_frames(path, purpose=terminal_frame_purpose(Path(relative))))
                     if path.suffix == ".log" else io.read_bytes(path, purpose="state"))
        assert recovered == data
        assert artifacts.read_bytes(path, home=home) == data
        if path.suffix == ".png":
            assert read_owned_media(path) == data
    assert scan_for_plaintext(home) == []
    assert public.read_bytes() == b"synthetic public package archive"
    vault.lock_now(home)
    assert set(scan_for_plaintext(home)) == set(payloads)


@pytest.mark.parametrize("family", ["cache/terminal-output", "artifacts/terminal-output"])
def test_only_offline_repair_rekeys_unbound_legacy_terminal_frames(legacy_home, family):
    home, _, _ = legacy_home
    relative = Path(family) / "previous-codec.log"
    target = home / relative
    for parent in reversed(target.parents):
        if parent.is_relative_to(home):
            parent.mkdir(mode=0o700, exist_ok=True)
            parent.chmod(0o700)
    message = b"synthetic legacy terminal frame must survive repair\n"
    frames.append(target, message, purpose="log")
    before = target.read_bytes()
    assert relative.as_posix() in scan_for_plaintext(home)
    with pytest.raises(VaultError):
        artifacts.read_bytes(target, home=home)
    assert target.read_bytes() == before
    repaired = repair_clobbered_state(home)
    assert repaired["unrecoverable"] == []
    assert relative.as_posix() in repaired["reframed"]
    assert artifacts.read_bytes(target, home=home) == message
    with pytest.raises(VaultError):
        list(frames.read_frames(target, purpose="log"))
    after = target.read_bytes()
    assert repair_clobbered_state(home)["reframed"] == []
    assert target.read_bytes() == after and scan_for_plaintext(home) == []
    moved = target.with_name("path-swap.log")
    moved.write_bytes(after)
    moved.chmod(0o600)
    refused = repair_clobbered_state(home)
    assert any(moved.relative_to(home).as_posix() in entry for entry in refused["unrecoverable"])
    assert moved.read_bytes() == after
    assert artifacts.read_bytes(target, home=home) == message


def test_initial_migration_binds_terminal_frames_to_final_path(tmp_path, monkeypatch):
    home = tmp_path / "migrate-owner"
    relative = Path("cache/terminal-output/out-migrate.log")
    path = home / relative
    path.parent.mkdir(parents=True)
    message = b"synthetic initial terminal migration\nunterminated tail"
    path.write_bytes(message)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    try:
        assert migrate_home(home, "synthetic bound migration credential").ok
        vault.unlock(home, "synthetic bound migration credential")
        assert artifacts.read_bytes(path, home=home) == message
        assert b"".join(frames.read_frames(path, purpose=terminal_frame_purpose(relative))) == message
        with pytest.raises(VaultError):
            list(frames.read_frames(path, purpose="log"))
        assert scan_for_plaintext(home) == []
    finally:
        vault.clear_vault_cache()


@pytest.mark.platforms("posix")
@pytest.mark.require_symlinks
def test_alias_frame_scope_recovers_original_and_rejects_transplanted_ciphertext(tmp_path, monkeypatch):
    from hermes_security.migrate import _frame_purpose

    home, alias = tmp_path / "physical-owner", tmp_path / "logical-home"
    home.mkdir()
    alias.symlink_to(home, target_is_directory=True)
    monkeypatch.setenv("HERMES_HOME", str(alias))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(home, "synthetic alias frame password")
    vault.unlock(home, "synthetic alias frame password")
    relative = Path("artifacts/terminal-output/original.log")
    message = b"synthetic private alias frame\ncomplete tail"
    try:
        original = artifacts.write_bytes(home / relative, message, home=home)
        ciphertext = original.read_bytes()
        assert b"".join(frames.read_frames(alias / relative, purpose=_frame_purpose(alias / relative))) == message
        assert _frame_purpose(relative) == _frame_purpose(home / relative)
        copied = original.with_name("different-artifact.log")
        copied.write_bytes(ciphertext)
        copied.chmod(0o600)
        with pytest.raises(VaultError):
            list(frames.read_frames(copied, purpose=_frame_purpose(alias / copied.relative_to(home))))
        assert original.read_bytes() == ciphertext
        assert artifacts.read_bytes(original, home=home) == message
    finally:
        vault.clear_vault_cache()
