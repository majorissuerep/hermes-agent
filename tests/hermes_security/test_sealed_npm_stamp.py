"""Fork: the npm lockfile stamp (.npm_lock_hash_*) is sealed state in a vaulted home.

Regression: ``hermes update`` post-swap crashed with UnicodeDecodeError reading the
sealed stamp as plaintext (byte 0xd9 at offset 11 — envelope ciphertext); update_cmd_deps
must read/write it envelope-aware like every other home-root state file.
"""

from pathlib import Path

import pytest

from hermes_cli import update_cmd as up
from hermes_cli import update_cmd_deps as deps
from hermes_security import vault as hv


@pytest.fixture
def vaulted_home(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    hv.init_vault(home, "stamp-pw", _allow_existing_state=True)
    hv.unlock(home, "stamp-pw")
    yield home
    hv.lock_now(home)


def _bind_project_root(monkeypatch, root: Path):
    monkeypatch.setattr(up, "PROJECT_ROOT", root, raising=False)
    monkeypatch.setattr(deps, "_m", lambda: up, raising=False)


def test_sealed_stamp_roundtrip(vaulted_home, monkeypatch):
    """Write via the fixed writer, read back via the fixed reader — both sealed."""
    _bind_project_root(monkeypatch, vaulted_home.parent)

    deps._record_npm_lockfile_hash(vaulted_home)
    raw = deps._npm_lock_cache_file(vaulted_home).read_bytes()
    assert raw.startswith(b"HRMVAULT"), "stamp must be a sealed envelope in-home"

    current = deps._npm_manifests_digest()
    assert current is not None
    assert deps._npm_stamp_matches(vaulted_home, current) is True


def test_sealed_stamp_never_crashes_the_update_path(vaulted_home, monkeypatch):
    """The exact incident shape: sealed/garbage stamp + read -> must not raise, must not match."""
    _bind_project_root(monkeypatch, vaulted_home.parent)

    # Ciphertext that is not a parseable envelope (worst case: corrupt/interrupted write).
    deps._npm_lock_cache_file(vaulted_home).write_bytes(b"HRMVAULT\x00\xd9invalid-not-an-envelope")
    assert deps._npm_stamp_matches(vaulted_home, "deadbeef") is False


def test_plaintext_stamp_still_matches_outside_home(monkeypatch, tmp_path):
    """No vault -> plain file semantics preserved (stamps in test/CI checkouts)."""
    _bind_project_root(monkeypatch, tmp_path)

    deps._record_npm_lockfile_hash(tmp_path)
    assert not deps._npm_lock_cache_file(tmp_path).read_bytes().startswith(b"HRMVAULT")
    current = deps._npm_manifests_digest()
    assert current is not None
    assert deps._npm_stamp_matches(tmp_path, current) is True


def test_missing_stamp_never_matches(tmp_path):
    assert deps._npm_stamp_matches(tmp_path, "deadbeef") is False
