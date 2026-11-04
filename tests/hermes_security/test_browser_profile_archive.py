"""Owned browser-profile archives: authenticated, path/identity bound, and never model artifacts.

Contracts for ``hermes_security.browser_profile_archive`` against a synthetic Chromium tree with a
real SQLite cookie store. Only the vault KDF cost is lowered; the crypto and file I/O are real.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import os
import stat

import pytest

from hermes_security import artifacts
from hermes_security import browser_profile_archive as archive
from hermes_security.errors import VaultError
from tests.hermes_cli.browser_profile_env import (
    CANARY, chromium_tree, cookie_db, owned_profile_env, plaintext_hits, reset_owned_profiles)

SLOT = archive.DEBUG_SLOT


@pytest.fixture
def home(tmp_path, monkeypatch):
    selected, _ram = owned_profile_env(tmp_path, monkeypatch)
    yield selected
    reset_owned_profiles()


def _write(home: Path, source: Path, *, slot=SLOT, identity=None):
    custody = archive.capture_custody(home)
    identity = identity or {}
    digest = archive.identity_digest(custody, slot, identity)
    relative = archive.archive_relative(slot, digest)
    archive.write_archive(home, relative, source, custody=custody, slot=slot, identity=identity, digest=digest)
    return custody, relative, digest


def test_round_trip_restores_profile_state_and_persists_no_plaintext(home, tmp_path):
    source = chromium_tree(tmp_path / "profile")
    custody, relative, digest = _write(home, source)

    assert plaintext_hits(home) == []  # cookie DB and site storage are ciphertext on disk
    restored = tmp_path / "restored"
    restored.mkdir()
    summary = archive.replay_archive(home, relative, custody=custody, slot=SLOT, digest=digest, target=restored)

    assert summary == archive.tree_digest(source) == archive.tree_digest(restored)
    assert cookie_db(restored / "Default" / "Network" / "Cookies") == CANARY.decode()
    assert not (restored / "Default" / "Cache").exists()  # regenerable cache is not persisted
    assert not (restored / "SingletonLock").exists() and not (restored / "SingletonLock").is_symlink()


@pytest.mark.parametrize("damage", ["flip", "truncate", "append"])
def test_damaged_archive_is_rejected_before_anything_is_published(home, tmp_path, damage):
    _custody, relative, digest = _write(home, chromium_tree(tmp_path / "profile"))
    stored = home / Path(relative)
    raw = bytearray(stored.read_bytes())
    if damage == "flip":
        raw[len(raw) // 2] ^= 0x01
    elif damage == "truncate":
        raw = raw[: len(raw) - 40]
    else:
        raw += raw[:64]
    stored.write_bytes(bytes(raw))

    with pytest.raises(VaultError):
        archive.replay_archive(home, relative, custody=archive.capture_custody(home), slot=SLOT, digest=digest,
                               target=None)


def test_archive_is_bound_to_its_slot_identity_and_location(home, tmp_path):
    custody = archive.capture_custody(home)
    account_a = {"browser": "chrome", "source_profile": "Profile 2", "account": {"gaia_id": "a"}}
    account_b = {**account_a, "account": {"gaia_id": "b"}}
    slot = archive.real_slot("chrome")
    _custody, relative, digest_a = _write(home, chromium_tree(tmp_path / "profile"), slot=slot, identity=account_a)
    digest_b = archive.identity_digest(custody, slot, account_b)

    assert archive.archive_relative(slot, digest_a) != archive.archive_relative(slot, digest_b)
    with pytest.raises(archive.ArchiveError, match="different profile or account"):
        archive.replay_archive(home, relative, custody=custody, slot=slot, digest=digest_b)
    moved = PurePosixPath(archive.ARCHIVE_ROOT, "real-" + "0" * 32 + archive.ARCHIVE_SUFFIX)
    (home / Path(relative)).rename(home / Path(moved))
    with pytest.raises(VaultError):  # the key and associated data are bound to the archive path
        archive.replay_archive(home, moved, custody=custody, slot=slot, digest=digest_a)


def test_failed_persist_keeps_the_previous_archive_and_leaves_no_stage(home, tmp_path, monkeypatch):
    first = chromium_tree(tmp_path / "first", cookie="first-generation")
    custody, relative, digest = _write(home, first)
    published = (home / Path(relative)).read_bytes()
    second = chromium_tree(tmp_path / "second", cookie="second-generation")
    (second / "Default" / "big.bin").write_bytes(b"x" * 4096)
    monkeypatch.setattr(archive, "MAX_PROFILE_BYTES", 2048)

    with pytest.raises(archive.ArchiveError, match="persistence bound"):
        archive.write_archive(home, relative, second, custody=custody, slot=SLOT, identity={}, digest=digest)

    assert (home / Path(relative)).read_bytes() == published
    assert [p.name for p in (home / archive.ARCHIVE_ROOT).iterdir()] == [relative.name]
    assert (second / "Default" / "big.bin").stat().st_size == 4096  # nothing was discarded


@pytest.mark.platforms("posix")
def test_swapped_source_ancestor_is_never_followed_outside_the_profile(home, tmp_path, monkeypatch):
    source = chromium_tree(tmp_path / "source", stale_lock=False)
    custody, relative, digest = _write(home, source)
    previous = (home / Path(relative)).read_bytes()
    foreign = chromium_tree(tmp_path / "foreign", "foreign-cookie", stale_lock=False)
    original_open = os.open
    moved = source / "retained-default"
    swapped = False
    foreign_ids = {(p.stat().st_dev, p.stat().st_ino) for p in foreign.rglob("*") if p.is_file()}
    opened_foreign = []

    def racing_open(path, flags, *args, **kwargs):
        nonlocal swapped
        # Cover both full-path readers and descriptor-relative ancestor traversal.
        if not swapped and (str(path) == "Default" or str(path).startswith(str(source / "Default") + os.sep)):
            swapped = True
            (source / "Default").rename(moved)
            (source / "Default").symlink_to(foreign / "Default", target_is_directory=True)
        fd = original_open(path, flags, *args, **kwargs)
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) in foreign_ids:
            opened_foreign.append(str(path))
        return fd

    monkeypatch.setattr(os, "open", racing_open)
    with pytest.raises((OSError, archive.ArchiveError)):
        archive.write_archive(home, relative, source, custody=custody, slot=SLOT, identity={}, digest=digest)
    assert swapped and (home / Path(relative)).read_bytes() == previous
    assert opened_foreign == []
    assert cookie_db(foreign / "Default" / "Network" / "Cookies") == "foreign-cookie"
    assert cookie_db(moved / "Network" / "Cookies") == CANARY.decode()


def test_post_publication_fsync_failure_reports_uncertain_durability_and_retains_source(home, tmp_path, monkeypatch):
    source = chromium_tree(tmp_path / "source", stale_lock=False)
    custody = archive.capture_custody(home)
    digest = archive.identity_digest(custody, SLOT, {})
    relative = archive.archive_relative(SLOT, digest)
    original_sync = os.fsync

    def fail_parent(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("synthetic parent fsync failure")
        return original_sync(fd)

    monkeypatch.setattr(os, "fsync", fail_parent)
    with pytest.raises(archive.ArchiveError, match="published.*durability"):
        archive.write_archive(home, relative, source, custody=custody, slot=SLOT, identity={}, digest=digest)
    assert archive.replay_archive(home, relative, custody=custody) == archive.tree_digest(source)
    assert cookie_db(source / "Default" / "Network" / "Cookies") == CANARY.decode()


def test_archives_are_not_admitted_by_generic_artifact_or_file_tools(home, tmp_path):
    from agent.file_safety import get_read_block_error, is_write_denied

    _custody, relative, _digest = _write(home, chromium_tree(tmp_path / "profile"))
    stored = home / Path(relative)

    assert not artifacts.is_artifact_path(stored, home=home)
    with pytest.raises(PermissionError):
        artifacts.read_bytes(stored, home=home)
    assert get_read_block_error(str(stored)) and is_write_denied(str(stored))


@pytest.mark.parametrize("slot", [SLOT, archive.real_slot("chrome")], ids=["debug", "owned-real-copy"])
def test_persistent_application_state_survives_cache_shaped_names(home, tmp_path, slot):
    from tests.hermes_cli.browser_profile_env import application_state_tree

    source = chromium_tree(tmp_path / "profile")
    payloads = application_state_tree(source)
    discarded = ("Default/Cache", "Profile 2/Cache", "Default/Code Cache", "Default/GPUCache",
                 "ShaderCache", "GrShaderCache", "Crashpad", "BrowserMetrics")
    for name in discarded:
        directory = source / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "regenerable").write_bytes(b"synthetic-regenerable-browser-cache")
    for name in ("DevToolsActivePort", "RunningChromeVersion", "launch-stderr.log"):
        (source / name).write_text("synthetic previous runtime")
    (source / "SingletonSocket").symlink_to("synthetic-dead-browser-socket")
    custody, relative, digest = _write(home, source, slot=slot)
    restored = tmp_path / "restored"
    restored.mkdir()

    archive.replay_archive(home, relative, custody=custody, slot=slot, digest=digest, target=restored)

    for name, payload in payloads.items():
        assert (restored / name).read_bytes() == payload
        assert plaintext_hits(home, payload) == []
    assert all(not (restored / name).exists() for name in discarded)
    assert all(not (restored / name).exists() for name in
               ("DevToolsActivePort", "RunningChromeVersion", "launch-stderr.log"))
    assert not (restored / "SingletonSocket").is_symlink()
    assert not (restored / "SingletonLock").is_symlink()
