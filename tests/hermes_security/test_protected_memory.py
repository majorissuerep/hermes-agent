"""Unlocked vault keys live in wipeable locked custody, never in retained immutable bytes."""
import gc
import json
import os
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_security import vault
from hermes_security.errors import VaultLockedError
from hermes_security.passphrase import change_passphrase
from hermes_security.protected_memory import KeyCustody

ROOT = Path(__file__).resolve().parents[2]
_PASSWORD = "synthetic custody password"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    target = tmp_path / "owner"
    target.mkdir()
    vault.init_vault(target, _PASSWORD)
    yield target
    vault.clear_vault_cache()


@pytest.mark.parametrize("release", ["lock_now", "clear_vault_cache"])
def test_lock_wipes_the_retained_key_and_a_stale_vault_refuses(home, release):
    owner = vault.unlock(home, _PASSWORD)
    owner.write_bytes("config.yaml", b"model: synthetic\n", purpose="config")
    vault.lock_now(home) if release == "lock_now" else vault.clear_vault_cache()
    with pytest.raises(VaultLockedError):
        owner.master_key
    with pytest.raises(VaultLockedError):
        owner.read_bytes("config.yaml", purpose="config")
    reopened = vault.unlock(home, _PASSWORD)
    assert reopened is not owner
    assert reopened.read_bytes("config.yaml", purpose="config") == b"model: synthetic\n"
    with pytest.raises(VaultLockedError):
        owner.read_bytes("config.yaml", purpose="config")  # a stale vault is never renewed


def test_unlocked_vault_never_exposes_key_bytes_by_repr_or_pickle(home):
    owner = vault.unlock(home, _PASSWORD)
    key_hex = owner.master_key.hex()
    for text in (repr(owner), str(owner), repr(owner._custody)):
        assert key_hex not in text
    for held in (owner, owner._custody):
        with pytest.raises(TypeError):
            pickle.dumps(held)


def test_exported_key_is_a_caller_owned_copy_that_survives_lock(home):
    vault.unlock(home, _PASSWORD)
    exported = vault.export_unlocked_key(home)
    vault.lock_now(home)
    assert len(exported) == 32 and any(exported)
    assert vault.adopt_unlocked_key(home, exported).read_bytes is not None


def test_an_all_zero_key_is_a_legal_key(tmp_path):
    zero = bytes(32)
    custody = KeyCustody(zero)
    assert custody.export_copy() == zero
    custody.wipe()
    with pytest.raises(VaultLockedError):
        custody.export_copy()
    held = vault.Vault(tmp_path, zero)
    held.write_bytes("config.yaml", b"model: synthetic\n", purpose="config")
    assert held.read_bytes("config.yaml", purpose="config") == b"model: synthetic\n"
    assert len(vault.derive_purpose_key(zero, "metadata-auth")) == 32
    salt = os.urandom(16)
    assert vault._check_verifier(zero, salt, vault._make_verifier(zero, salt))
    held.wipe()


def _lock_during(monkeypatch, name, home):
    original = getattr(vault, name)

    def wiped_mid_operation(*args, **kwargs):
        vault.lock_now(home)
        return original(*args, **kwargs)

    monkeypatch.setattr(vault, name, wiped_mid_operation)


def _add_slot(home, owner):
    private, public = vault.generate_keypair()
    vault.add_key_slot(home, public_key_raw=public, unlocked_vault=owner)
    return lambda: vault.unlock_with_private_key(home, private)


def _rotate(home, _owner):
    change_passphrase(home, "rotated synthetic password")
    return lambda: vault.unlock(home, "rotated synthetic password")


@pytest.mark.parametrize(("seam", "publish"), [("_seal_to_public_key", _add_slot), ("_derive_root_key", _rotate)])
def test_a_lock_racing_key_metadata_never_publishes_a_zero_key(home, monkeypatch, seam, publish):
    owner = vault.unlock(home, _PASSWORD)
    owner.write_bytes("config.yaml", b"model: synthetic\n", purpose="config")
    _lock_during(monkeypatch, seam, home)
    try:
        reopen = publish(home, owner)
    except VaultLockedError:
        return  # refused before anything was published
    monkeypatch.undo()
    vault.clear_vault_cache()
    assert reopen().read_bytes("config.yaml", purpose="config") == b"model: synthetic\n"


def _locked_kib() -> int:
    with open("/proc/self/status", encoding="ascii") as stream:
        for line in stream:
            if line.startswith("VmLck:"):
                return int(line.split()[1])
    raise AssertionError("VmLck is missing from /proc/self/status")


def _memlock_allows_a_page() -> bool:
    import resource

    soft = resource.getrlimit(resource.RLIMIT_MEMLOCK)[0]
    return soft == resource.RLIM_INFINITY or soft >= 4096


@pytest.mark.platforms("linux")
def test_unlocked_key_pages_are_resident_locked_until_lock(home):
    if not _memlock_allows_a_page():
        pytest.skip("RLIMIT_MEMLOCK below one page on this runner")
    before = _locked_kib()
    vault.unlock(home, _PASSWORD)
    assert _locked_kib() >= before + 4, "the master key is not in mlock'd memory"
    vault.lock_now(home)
    assert _locked_kib() == before


@pytest.mark.platforms("linux")
def test_abandoned_custody_is_wiped_and_unlocked():
    if not _memlock_allows_a_page():
        pytest.skip("RLIMIT_MEMLOCK below one page on this runner")
    before = _locked_kib()
    custody = KeyCustody(bytes(range(32)))
    assert _locked_kib() >= before + 4
    del custody
    gc.collect()
    assert _locked_kib() == before


_NO_MEMLOCK = """
import json, resource, sys
sys.path.insert(0, sys.argv[1])
from hermes_security import vault
from hermes_security.protected_memory import KeyCustodyUnavailable
resource.setrlimit(resource.RLIMIT_MEMLOCK, (0, resource.getrlimit(resource.RLIMIT_MEMLOCK)[1]))
try:
    vault.unlock(sys.argv[2], sys.stdin.readline().rstrip("\\n"))
    outcome = "unlocked"
except KeyCustodyUnavailable as exc:
    outcome = str(exc)
locked_kib = [line.split()[1] for line in open("/proc/self/status") if line.startswith("VmLck:")][0]
print(json.dumps({"outcome": outcome, "unlocked": vault.is_unlocked(sys.argv[2]), "locked_kib": locked_kib}))
"""


@pytest.mark.platforms("linux")
@pytest.mark.skipif(os.geteuid() == 0, reason="root (CAP_IPC_LOCK) ignores RLIMIT_MEMLOCK")
def test_unlock_fails_closed_when_the_host_refuses_mlock(home):
    env = {name: value for name, value in os.environ.items() if not name.startswith("HERMES_VAULT")}
    result = subprocess.run([sys.executable, "-I", "-c", _NO_MEMLOCK, str(ROOT), str(home)], input=_PASSWORD + "\n",
                            env=env, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["unlocked"] is False
    assert report["locked_kib"] == "0"
    assert "ulimit -l" in report["outcome"] and "memlock soft limit 0 bytes" in report["outcome"]
    assert _PASSWORD not in result.stdout + result.stderr
