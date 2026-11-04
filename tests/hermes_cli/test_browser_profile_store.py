"""Owned browser profiles stay usable while their durable copy stays encrypted.

Lifecycle contracts for ``hermes_cli.browser_profile_store``: the active copy exists only on the
(RAM-proven) workspace base, a browser's exit is the save boundary, busy copies are never saved or
replaced, failures keep every existing copy, and saves use the custody of the request that prepared
the copy. The RAM proof is the labelled unit seam (``browser_profile_env``); the browser process is
a real exited child; vault crypto and file I/O are real.
"""
from __future__ import annotations

import os
import socket
import stat
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli import browser_profile_store as store
from hermes_security import browser_profile_archive as archive
from hermes_security import vault
from hermes_security.errors import VaultError
from tests.hermes_cli.browser_profile_env import (
    CANARY, PASSWORD, chromium_tree, cookie_db, exited_browser, owned_profile_env, plaintext_hits,
    reset_owned_profiles)

REAL = archive.real_slot("chrome")
IDENTITY_A = {"browser": "chrome", "source_dir": "/synthetic/a", "source_profile": "Profile 2",
              "account": {"gaia_id": "111", "user_name": "a@example.invalid"}}
IDENTITY_B = {**IDENTITY_A, "account": {"gaia_id": "222", "user_name": "b@example.invalid"}}


def test_revoked_launch_cannot_change_the_profile_binding(env):
    home, _ram = env
    profile = store.owned_profile(REAL)
    with store.prepare(profile, IDENTITY_A) as path:
        binding = path / archive.RUNTIME_BINDING_NAME
        before = binding.read_bytes()
        vault.lock_now(home)
        with pytest.raises(VaultError):
            store.record_launch(profile, SimpleNamespace(pid=os.getpid()))
        assert binding.read_bytes() == before


def test_consent_cleanup_retains_archive_when_a_profile_lock_is_unavailable(env, monkeypatch):
    home, _ram = env
    _session(REAL, IDENTITY_A, "previous-cookie")
    assert store.settle() == {REAL: "saved"}
    stored = archive.archive_paths(home)[0]
    before = stored.read_bytes()
    _profile, active = _session(REAL, IDENTITY_A, "current-cookie")

    @contextmanager
    def busy(_profile, **_kwargs):
        raise store._LockBusy("another owner is saving")
        yield  # contextmanager protocol: the refusal happens on entry

    monkeypatch.setattr(store, "_profile_lock", busy)
    report = store.discard_real_profiles(["chrome"])
    assert stored.read_bytes() == before
    assert cookie_db(active / "Default" / "Network" / "Cookies") == "current-cookie"
    assert any("profile lock unavailable; repeat cleanup" in item for item in report["retained"])


@pytest.mark.parametrize("parent_mode", [None, pytest.param(0o2700, marks=pytest.mark.platforms("linux"),
                                                        id="setgid-parent")])
def test_replaced_ram_directory_cannot_reuse_a_copied_lease_binding(env, parent_mode):
    home, _ram = env
    if parent_mode is not None:
        os.chown(_ram, -1, os.getegid())  # native SGID inheritance with the fixture's own group
        _ram.chmod(parent_mode)
        assert stat.S_IMODE(_ram.stat().st_mode) == parent_mode
    _profile, path = _session(REAL, IDENTITY_A, "original-cookie")
    original = path.with_name(path.name + ".retained")
    path.rename(original)
    path.mkdir(mode=0o700)
    if parent_mode is not None:
        assert stat.S_IMODE(path.stat().st_mode) == 0o2700
    path.chmod(0o700)  # explicit replacement precondition: mkdir inherits an operator parent's SGID
    assert stat.S_IMODE(path.stat().st_mode) == 0o700
    binding = archive.RUNTIME_BINDING_NAME
    (path / binding).write_bytes((original / binding).read_bytes())
    cookie_db(path / "Default" / "Network" / "Cookies", "replacement-cookie")

    result = store.settle()
    assert "directory was replaced" in result[REAL]
    assert archive.archive_paths(home) == []
    assert cookie_db(original / "Default" / "Network" / "Cookies") == "original-cookie"
    assert cookie_db(path / "Default" / "Network" / "Cookies") == "replacement-cookie"
    if parent_mode is not None:
        assert stat.S_IMODE(_ram.stat().st_mode) == parent_mode


@pytest.fixture
def env(tmp_path, monkeypatch):
    yield owned_profile_env(tmp_path, monkeypatch)
    reset_owned_profiles()


def _session(slot, identity, cookie):
    """One browser session on the copy: prepare, browser writes state, browser launches and exits."""
    profile = store.owned_profile(slot)
    with store.prepare(profile, identity) as path:
        cookie_db(path / "Default" / "Network" / "Cookies", cookie)
        store.record_launch(profile, exited_browser())
    return profile, path


def test_browser_exit_saves_encrypted_and_next_session_restores(env):
    home, ram = env
    profile, path = _session(REAL, IDENTITY_A, CANARY.decode())
    assert path.parent == ram.resolve() and (path.stat().st_mode & 0o777) == 0o700

    assert store.settle() == {REAL: "saved"}
    assert not path.exists()  # RAM copy retired only after the verified save
    assert len(archive.archive_paths(home)) == 1 and plaintext_hits(home) == []

    with store.prepare(profile, IDENTITY_A) as restored:
        assert cookie_db(restored / "Default" / "Network" / "Cookies") == CANARY.decode()


def test_copy_held_by_a_live_browser_is_never_saved_or_retired(env):
    home, _ram = env
    profile, path = _session(REAL, IDENTITY_A, "live-session")
    # Chromium's own singleton lock naming a live local process (this test process).
    os.symlink(f"{socket.gethostname()}-{os.getpid()}", path / "SingletonLock")

    assert store.settle() == {REAL: "busy"}
    assert path.is_dir() and archive.archive_paths(home) == []
    with pytest.raises(store.BrowserProfileError, match="in use"):
        with store.prepare(profile, IDENTITY_A):
            pass

    (path / "SingletonLock").unlink()
    assert store.settle() == {REAL: "saved"}


def test_failed_save_keeps_ram_copy_and_previous_archive(env, monkeypatch):
    home, _ram = env
    _profile, _path = _session(REAL, IDENTITY_A, "first-generation")
    assert store.settle() == {REAL: "saved"}
    previous = archive.archive_paths(home)[0].read_bytes()

    _profile, path = _session(REAL, IDENTITY_A, "second-generation")
    monkeypatch.setattr(archive, "MAX_PROFILE_BYTES", 1024)
    assert store.settle() == {REAL: "unsaved"}

    assert cookie_db(path / "Default" / "Network" / "Cookies") == "second-generation"
    assert archive.archive_paths(home)[0].read_bytes() == previous
    assert any("unsaved changes held only in RAM" in line and "last save failed" in line
               for line in store.status_lines(home, ["chrome"]))


def test_corrupt_archive_refuses_instead_of_starting_an_empty_profile(env):
    home, ram = env
    profile, _path = _session(archive.DEBUG_SLOT, {}, "debug-session")
    assert store.settle() == {archive.DEBUG_SLOT: "saved"}
    stored = archive.archive_paths(home)[0]
    damaged = bytearray(stored.read_bytes())
    damaged[-20] ^= 0xFF
    stored.write_bytes(bytes(damaged))

    with pytest.raises(store.BrowserProfileError, match="did not start an empty profile"):
        with store.prepare(profile, {}):
            pass
    assert not profile.path.exists() and stored.read_bytes() == bytes(damaged)
    assert [p for p in ram.iterdir() if not p.name.endswith(".lock")] == []


def test_each_source_identity_restores_only_its_own_state(env):
    home, _ram = env
    _session(REAL, IDENTITY_A, "account-a-cookie")
    assert store.settle() == {REAL: "saved"}
    profile, _path = _session(REAL, IDENTITY_B, "account-b-cookie")
    assert store.settle() == {REAL: "saved"}
    assert len(archive.archive_paths(home)) == 2

    with store.prepare(profile, IDENTITY_A) as restored:
        assert cookie_db(restored / "Default" / "Network" / "Cookies") == "account-a-cookie"


def test_revoked_original_custody_never_adopts_a_later_unlock(env):
    home, _ram = env
    profile, path = _session(REAL, IDENTITY_A, CANARY.decode())
    vault.lock_now(home)

    assert store.settle() == {REAL: "revoked"}
    assert path.is_dir() and archive.archive_paths(home) == []  # RAM copy kept, nothing written

    vault.unlock(home, PASSWORD)  # a later, independent unlock
    assert store.settle() == {}  # the revoked owner is gone; it does not borrow the new unlock
    assert archive.archive_paths(home) == []
    with store.prepare(profile, IDENTITY_A) as current:  # a new request checkpoints the RAM copy
        assert current == path
    assert len(archive.archive_paths(home)) == 1


def test_unproven_ram_refuses_and_creates_nothing(tmp_path, monkeypatch):
    home, ram = owned_profile_env(tmp_path, monkeypatch, proven=False)
    try:
        with pytest.raises(store.BrowserProfileError, match="security.protected_work_dir"):
            with store.prepare(store.owned_profile(archive.DEBUG_SLOT), {}):
                pass
        assert list(ram.iterdir()) == [] and not (home / archive.ARCHIVE_ROOT).exists()
    finally:
        reset_owned_profiles()


def test_legacy_plaintext_profile_requires_explicit_repair_and_is_untouched(env):
    home, ram = env
    legacy = chromium_tree(home / "chrome-debug")
    before = archive.tree_digest(legacy)

    with pytest.raises(store.BrowserProfileError, match="hermes secure-vault repair"):
        with store.prepare(store.owned_profile(archive.DEBUG_SLOT), {}):
            pass
    assert archive.tree_digest(legacy) == before
    assert [p for p in ram.iterdir() if not p.name.endswith(".lock")] == []


@pytest.fixture
def native_ram(tmp_path, monkeypatch):
    """A vaulted home plus a real writable mount the LIVE prover accepts as RAM-only; else skip."""
    import tempfile

    from hermes_security.protected_workspace import protected_workspace_status

    home = tmp_path / "native-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    with open("/proc/self/mountinfo", encoding="utf-8") as handle:
        mounts = [line.split(" - ", 1) for line in handle if " - " in line]
    for head, tail in mounts:
        point = head.split()[4].replace("\\040", " ")
        if tail.split()[0] not in {"ramfs", "tmpfs"} or not os.access(point, os.W_OK | os.X_OK):
            continue
        base = Path(tempfile.mkdtemp(dir=point, prefix="hermes-test-runtime-"))
        if protected_workspace_status(base).proven:
            monkeypatch.setenv("XDG_RUNTIME_DIR", str(base))
            vault.init_vault(home, PASSWORD)
            vault.unlock(home, PASSWORD)
            yield home, base
            reset_owned_profiles()
            import shutil
            shutil.rmtree(base)
            return
        os.rmdir(base)
    pytest.skip("this host has no writable RAM-only mount that is provably never swapped")


@pytest.mark.platforms("linux")
def test_native_ram_proof_session_round_trip(native_ram):
    home, base = native_ram
    profile, path = _session(archive.DEBUG_SLOT, {}, CANARY.decode())
    assert path.parent == base.resolve()

    assert store.settle() == {archive.DEBUG_SLOT: "saved"}
    assert plaintext_hits(home) == []
    with store.prepare(profile, {}) as restored:
        assert cookie_db(restored / "Default" / "Network" / "Cookies") == CANARY.decode()


def test_consent_off_discards_real_copies_but_not_debug_profile_or_live_browser(env):
    home, _ram = env
    _session(archive.DEBUG_SLOT, {}, "debug-session")
    _session(REAL, IDENTITY_A, "real-session")
    assert store.settle() == {archive.DEBUG_SLOT: "saved", REAL: "saved"}
    _profile, path = _session(archive.real_slot("edge"), IDENTITY_A, "edge-live")
    os.symlink(f"{socket.gethostname()}-{os.getpid()}", path / "SingletonLock")

    report = store.discard_real_profiles(["chrome", "edge"])

    assert [p.name for p in archive.archive_paths(home)] == [archive.DEBUG_SLOT + archive.ARCHIVE_SUFFIX]
    assert path.is_dir() and any("edge" in item for item in report["retained"])

    (path / "SingletonLock").unlink()  # that browser exits: its copy is discarded, never re-archived
    assert store.settle() == {archive.real_slot("edge"): "discarded"}
    assert not path.exists()
    assert [p.name for p in archive.archive_paths(home)] == [archive.DEBUG_SLOT + archive.ARCHIVE_SUFFIX]


def test_stale_lease_never_saves_or_retires_a_copy_another_request_claimed(env):
    home, _ram = env
    profile, path = _session(REAL, IDENTITY_A, "first-request")
    stale = store._LEASES[(profile.home, profile.slot)]
    with store.prepare(profile, IDENTITY_A):  # a newer request re-claims the same copy
        pass

    assert store._settle_lease(stale) == "superseded"
    assert path.is_dir() and archive.archive_paths(home) == []


def test_restored_copy_is_not_touched_after_its_custody_is_revoked(env):
    home, _ram = env
    profile, path = _session(REAL, IDENTITY_A, "saved-then-locked")
    assert store.settle() == {REAL: "saved"}
    with store.prepare(profile, IDENTITY_A):
        pass
    vault.lock_now(home)
    vault.unlock(home, PASSWORD)  # a later unlock never revives the original request's lease

    assert store.settle() == {REAL: "revoked"}
    assert path.is_dir() and len(archive.archive_paths(home)) == 1
