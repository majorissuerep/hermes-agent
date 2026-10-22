"""Fork regression: a wrong vault key opening the kanban board must be a
credential failure, never "corrupt".

Incident (manual QA emulation, 2026-10-22): ``kanban_home()`` resolves the board
across profiles BY DESIGN (shared default root). When that root predates the
vault (legacy plaintext board, no ``.hermes-vault``), ``find_vault_home`` falls
back to the ACTIVE home — so a profile process (or any ``HERMES_HOME`` override
with its own unlocked vault) routes the plaintext board into SQLCipher, the
derived wrong key fails the page-1 hmac, and the corruption guard QUARANTINES a
healthy legacy board (``.corrupt.*.bak``) with "file is not a database".
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pytest

from hermes_security import migrate as mig
from hermes_security import vault as hv

PW = "kanban-wrongkey-pw"


@pytest.fixture()
def legacy_shared_board_and_profile_home(tmp_path, monkeypatch):
    """Legacy shape: plaintext kanban.db in a VAULTLESS shared root; a profile
    home with its own vault unlocked through an env key file (the exact
    old-install + profile combination that false-flags the board)."""
    root = tmp_path / "synhome"
    shared = root / ".hermes"
    shared.mkdir(parents=True)
    board = shared / "kanban.db"
    conn = sqlite3.connect(board)
    conn.execute("CREATE TABLE tasks (id TEXT)")
    conn.execute("INSERT INTO tasks VALUES ('t1')")
    conn.commit()
    conn.close()

    prof = root / "profile-home"
    prof.mkdir()
    (prof / "config.yaml").write_text("model:\n  provider: auto\n")
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("HERMES_HOME", str(prof))
    assert mig.migrate_home(prof, PW).ok
    hv.clear_vault_cache()

    # Key slot so the env unlock path (not the in-process cache) resolves the
    # profile vault while the shared root stays vaultless.
    from hermes_security.vault import add_key_slot

    hv.unlock(prof, PW)
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    priv = X25519PrivateKey.generate()
    pub_raw = priv.public_key().public_bytes_raw()
    add_key_slot(prof, public_key_raw=pub_raw, unlocked_vault=hv.get_vault(prof))
    key_file = root / "prof.key"
    import base64

    key_file.write_bytes(base64.urlsafe_b64encode(priv.private_bytes_raw()))
    key_file.chmod(0o600)
    hv.clear_vault_cache()
    monkeypatch.setenv("HERMES_VAULT_PRIVATE_KEY", str(key_file))
    yield root, shared, prof, board
    hv.clear_vault_cache()
    monkeypatch.delenv("HERMES_VAULT_PRIVATE_KEY", raising=False)


def test_wrong_vault_key_is_not_corruption(legacy_shared_board_and_profile_home):
    _root, shared, _prof, board = legacy_shared_board_and_profile_home
    from hermes_cli import kanban_db_connect as kbc
    from hermes_security.errors import VaultKeyMismatchError

    before = board.read_bytes()
    with pytest.raises(VaultKeyMismatchError) as excinfo:
        kbc.connect(board)
    msg = str(excinfo.value)
    assert "corrupt" not in msg.lower(), msg
    assert "vault" in msg.lower() and "key" in msg.lower()
    # No quarantine may be minted against the healthy legacy board.
    assert not list(shared.glob("kanban.db.corrupt*"))
    assert board.read_bytes() == before  # untouched


def test_repair_db_reports_credential_not_corrupt(legacy_shared_board_and_profile_home):
    """The explicit repair surface must not "repair" (back up + condemn) a board
    it simply cannot authenticate."""
    _root, shared, _prof, board = legacy_shared_board_and_profile_home
    from hermes_cli import kanban_db_connect as kbc
    from hermes_security.errors import VaultKeyMismatchError

    with pytest.raises(VaultKeyMismatchError):
        kbc.repair_db(board)
    assert not list(shared.glob("kanban.db.corrupt*"))
