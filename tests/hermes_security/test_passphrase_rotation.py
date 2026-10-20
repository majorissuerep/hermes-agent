"""Password rewrapping changes credentials, never encrypted state or child authority."""
import json
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from hermes_security import vault
from hermes_security.errors import VaultError, VaultIntegrityError, VaultLockedError, WrongMasterPasswordError
from hermes_security.passphrase import change_passphrase


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    target = tmp_path / "owner"
    target.mkdir()
    vault.init_vault(target, "old synthetic password")
    vault.unlock(target, "old synthetic password")
    yield target
    vault.clear_vault_cache()


def test_v1_rotation_preserves_ciphertext_sqlcipher_master_and_key_slots(home):
    from hermes_security import sqlite

    owner = vault.get_vault(home)
    private, public = vault.generate_keypair()
    vault.add_key_slot(home, public_key_raw=public, unlocked_vault=owner)
    owner.write_bytes("config.yaml", b"model: preserved\n", purpose="config")
    db = sqlite.connect(home / "state.db")
    db.execute("CREATE TABLE history (content TEXT)")
    db.execute("INSERT INTO history VALUES ('durable original history')")
    db.commit()
    db.close()
    paths = [home / "config.yaml", home / "state.db"]
    before = {path: path.read_bytes() for path in paths}
    old_meta = json.loads((home / ".hermes-vault").read_text())
    change_passphrase(home, "new synthetic password")
    meta = json.loads((home / ".hermes-vault").read_text())
    assert meta["version"] == 2 and vault._VERSION == 1
    assert meta["salt"] != old_meta["salt"]  # retired password's original KDF input is gone
    assert meta["key_slots"] == old_meta["key_slots"]
    assert "kdf_n" not in meta
    assert {path: path.read_bytes() for path in paths} == before
    vault.clear_vault_cache()
    with pytest.raises(WrongMasterPasswordError):
        vault.unlock(home, "old synthetic password")
    assert vault.unlock(home, "new synthetic password").master_key == owner.master_key
    db = sqlite.connect(f"file:{home / 'state.db'}?mode=ro", uri=True)
    assert db.execute("SELECT content FROM history").fetchall() == [("durable original history",)]
    db.close()
    vault.clear_vault_cache()
    assert vault.unlock_with_private_key(home, private).master_key == owner.master_key
    assert vault.get_vault(home).read_bytes("config.yaml", purpose="config") == b"model: preserved\n"


def test_repeated_rotation_keeps_master_proof_and_prepared_key_fd_valid(home, monkeypatch):
    from hermes_security.handoff import adopt_inherited_key, prepare_child_key

    owner = vault.get_vault(home)
    first, additions = prepare_child_key()
    try:
        change_passphrase(home, "second password")
        meta = json.loads((home / ".hermes-vault").read_text())
        change_passphrase(home, b"third password")
        later = json.loads((home / ".hermes-vault").read_text())
        assert (meta["salt"], meta["verifier"]) == (later["salt"], later["verifier"])
        vault.clear_vault_cache()
        monkeypatch.setenv("HERMES_VAULT_KEY_FD", additions["HERMES_VAULT_KEY_FD"])
        assert adopt_inherited_key()
        first = None  # adoption consumed and closed the descriptor
        assert vault.get_vault(home).master_key == owner.master_key
        vault.clear_vault_cache()
        with pytest.raises(WrongMasterPasswordError):
            vault.unlock(home, "second password")
        assert vault.unlock(home, "third password").master_key == owner.master_key
    finally:
        if first is not None:
            os.close(first)


def test_rotated_backup_reopens_with_new_password_without_reencrypting(home, tmp_path):
    vault.get_vault(home).write_bytes("auth.json", b'{"saved":true}', purpose="auth")
    change_passphrase(home, "backup credential")
    copy = tmp_path / "restored"
    shutil.copytree(home, copy)
    assert vault.unlock(copy, "backup credential").read_bytes("auth.json", purpose="auth") == b'{"saved":true}'
    vault.lock_now(copy)
    with pytest.raises(WrongMasterPasswordError):
        vault.unlock(copy, "old synthetic password")


@pytest.mark.parametrize("mutation", ["sealed", "nonce", "salt", "authentication", "kdf_n"])
def test_tampered_wrap_or_unbounded_kdf_is_refused_by_password_key_and_writer(home, mutation):
    owner = vault.get_vault(home)
    change_passphrase(home, "rotated credential")
    path = home / ".hermes-vault"
    meta = json.loads(path.read_text())
    if mutation == "authentication":
        meta[mutation] = vault._b64e(b"!" * 32)
    elif mutation == "kdf_n":
        meta["passphrase"][mutation] = 2 ** 40
    else:
        size = {"sealed": 48, "nonce": 12, "salt": 32}[mutation]
        meta["passphrase"][mutation] = vault._b64e(b"!" * size)
    path.write_text(json.dumps(meta))
    original = path.read_bytes()
    with pytest.raises(VaultError):
        change_passphrase(home, "must not repair tampering")
    assert path.read_bytes() == original
    vault.clear_vault_cache()
    with pytest.raises(VaultError):
        vault.adopt_unlocked_key(home, owner.master_key)
    with pytest.raises(VaultError):
        vault.unlock(home, "rotated credential")


@pytest.mark.parametrize("failure", ["replace", "fsync"])
def test_failed_publication_retains_previous_password_and_metadata(home, monkeypatch, failure):
    original = (home / ".hermes-vault").read_bytes()

    def deny(*args, **kwargs):
        raise OSError("synthetic metadata replacement failure")

    monkeypatch.setattr(vault.os, failure, deny)
    with pytest.raises(OSError, match="replacement failure"):
        change_passphrase(home, "uncommitted credential")
    assert (home / ".hermes-vault").read_bytes() == original
    assert list(home.glob("..hermes-vault.*")) == []
    vault.clear_vault_cache()
    assert vault.unlock(home, "old synthetic password")


def test_rotation_and_key_slot_writers_do_not_lose_concurrent_updates(home):
    owner = vault.get_vault(home)
    pairs = [vault.generate_keypair() for _ in range(4)]
    ready = threading.Barrier(5)

    def add(public):
        ready.wait(10)
        vault.add_key_slot(home, public_key_raw=public, unlocked_vault=owner)

    def rotate():
        ready.wait(10)
        change_passphrase(home, "concurrent credential")

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(add, public) for _, public in pairs] + [pool.submit(rotate)]
        for future in futures:
            future.result(15)
    meta = json.loads((home / ".hermes-vault").read_text())
    assert len(meta["key_slots"]) == 4
    for private, _ in pairs:
        vault.clear_vault_cache()
        assert vault.unlock_with_private_key(home, private).master_key == owner.master_key
    vault.remove_key_slot(home, index=0)
    refused = 0
    for private, _ in pairs:
        vault.clear_vault_cache()
        try:
            vault.unlock_with_private_key(home, private)
        except WrongMasterPasswordError:
            refused += 1
    assert refused == 1  # concurrent additions have deliberately unspecified slot order
    vault.clear_vault_cache()
    assert vault.unlock(home, "concurrent credential").master_key == owner.master_key


def test_locked_wrong_owner_ancestor_and_symlink_targets_fail_closed(home, tmp_path):
    owner = vault.get_vault(home)
    nested = home / "profiles" / "worker"
    nested.mkdir(parents=True)
    with pytest.raises(VaultError):
        change_passphrase(nested, "must not rotate ancestor")
    alias = tmp_path / "alias"
    alias.symlink_to(home, target_is_directory=True)
    with pytest.raises(VaultIntegrityError):
        change_passphrase(alias, "must not follow owner alias")
    other = tmp_path / "other"
    other.mkdir()
    vault.init_vault(other, "other password")
    with pytest.raises(VaultIntegrityError):
        vault.add_key_slot(other, public_key_raw=vault.generate_keypair()[1], unlocked_vault=owner)
    vault.lock_now(home)
    with pytest.raises(VaultLockedError):
        change_passphrase(home, "must unlock first")


def test_stale_owner_cannot_mutate_replaced_vault_or_symlinked_metadata(home, tmp_path):
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    vault.init_vault(replacement, "different owner password")
    path = home / ".hermes-vault"
    path.write_bytes((replacement / ".hermes-vault").read_bytes())
    original = path.read_bytes()
    with pytest.raises(VaultError):
        change_passphrase(home, "must reject stale master")
    assert path.read_bytes() == original
    path.unlink()
    path.symlink_to(replacement / ".hermes-vault")
    with pytest.raises(VaultIntegrityError):
        change_passphrase(home, "must not mutate foreign metadata")
    assert (replacement / ".hermes-vault").read_bytes() == original


def test_metadata_lock_is_bounded_and_never_unlinked(home, monkeypatch):
    import fcntl
    from hermes_security import vault_metadata

    lock = home / ".hermes-vault.lock"
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        monkeypatch.setattr(vault_metadata, "_LOCK_TIMEOUT", 0)
        with pytest.raises(VaultError, match="busy"):
            change_passphrase(home, "cannot bypass an owned transaction")
        assert lock.stat().st_ino == os.fstat(fd).st_ino
    finally:
        os.close(fd)


def test_uncooperative_metadata_change_is_preserved_not_overwritten(home, monkeypatch):
    path = home / ".hermes-vault"
    derive = vault._derive_root_key
    changed = []

    def concurrent_change(*args):
        meta = json.loads(path.read_text())
        meta["external-writer"] = "must survive"
        path.write_text(json.dumps(meta))
        changed.append(path.read_bytes())
        return derive(*args)

    monkeypatch.setattr(vault, "_derive_root_key", concurrent_change)
    with pytest.raises(VaultIntegrityError, match="changed"):
        change_passphrase(home, "must not overwrite the newer metadata")
    assert path.read_bytes() == changed[0]


def test_legacy_metadata_kdf_limits_are_checked_before_expensive_derivation(home, monkeypatch):
    path = home / ".hermes-vault"
    meta = json.loads(path.read_text())
    meta["kdf_n"] = 2 ** 40
    path.write_text(json.dumps(meta))
    vault.clear_vault_cache()

    def forbidden(*args):
        raise AssertionError("untrusted oversized KDF reached derivation")

    monkeypatch.setattr(vault, "_derive_root_key", forbidden)
    with pytest.raises(VaultIntegrityError, match="KDF"):
        vault.unlock(home, "old synthetic password")
