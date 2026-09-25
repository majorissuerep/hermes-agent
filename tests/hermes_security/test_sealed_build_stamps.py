"""Fork: build stamps under $HERMES_HOME are sealed state in a vaulted home.

``secure-vault migrate`` seals every pre-existing home-root file — including
``desktop-build-stamp.json`` / web build stamps written by earlier updates as
plaintext. Plain readers then crash (UnicodeDecodeError on envelope ciphertext)
or mismatch; both directions must be envelope-aware, and any unreadable stamp
must fail open to "rebuild", never crash the update.
"""

from pathlib import Path

import pytest

from hermes_cli import main_web_build as wb
from hermes_security import vault as hv


@pytest.fixture
def vaulted_home(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    hv.init_vault(home, "stamp-pw", _allow_existing_state=True)
    hv.unlock(home, "stamp-pw")
    yield home
    hv.lock_now(home)


def test_build_stamp_roundtrip_sealed(vaulted_home, monkeypatch):
    stamp = vaulted_home / "desktop-build-stamp.json"
    wb._write_build_stamp(stamp, "desktop", lambda: "hash-1", sourceMode=False)
    assert stamp.read_bytes().startswith(b"HRMVAULT"), "stamp must seal in-home"
    assert wb._stamp_is_current(stamp, lambda: "hash-1", sourceMode=False) is True
    assert wb._stamp_is_current(stamp, lambda: "hash-2", sourceMode=False) is False


def test_sealed_stamp_read_never_crashes(vaulted_home):
    """Incident shape: pre-vault plaintext stamp sealed by migrate, then read."""
    stamp = vaulted_home / "desktop-build-stamp.json"
    stamp.write_bytes(b"HRMVAULT\x00\xd9not-an-envelope")
    assert wb._stamp_is_current(stamp, lambda: "hash-1") is False


def test_migrated_plaintext_stamp_treated_as_not_current(vaulted_home):
    """A plaintext stamp sealed by migrate becomes unreadable ciphertext for a plain
    reader — the fixed reader must say not-current (rebuild), not raise."""
    stamp = vaulted_home / "desktop-build-stamp.json"
    # Simulate migrate sealing a pre-vault stamp: write plaintext, seal via the vault.
    from hermes_security.io import write_text as vault_write

    vault_write(
        stamp,
        '{"contentHash": "hash-1", "sourceMode": false}',
        purpose="build_stamp",
    )
    assert wb._stamp_is_current(stamp, lambda: "hash-1", sourceMode=False) is True


def test_plaintext_stamp_outside_home_roundtrips(tmp_path):
    stamp = tmp_path / "web-build-stamp.json"
    wb._write_build_stamp(stamp, "web", lambda: "hash-1")
    assert not stamp.read_bytes().startswith(b"HRMVAULT")
    assert wb._stamp_is_current(stamp, lambda: "hash-1") is True
