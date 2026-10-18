"""Synthetic multi-process state uses the real vault gate and no plaintext fallback."""
import sqlite3

import pytest

from tests.e2e.core.sqlite._helpers import Chamber, integrity_rows


@pytest.mark.platforms("linux")
def test_child_openers_and_public_cli_share_only_sealed_vault_authority(tmp_path):
    chamber = Chamber(tmp_path, journal="delete")
    try:
        chamber.spawn("opener", "native")
        assert chamber.reap("native") == 0, chamber.stderr("native")
        assert chamber.db.read_bytes()[:16] != b"SQLite format 3\x00"
        with sqlite3.connect(chamber.db) as unkeyed:
            with pytest.raises(sqlite3.DatabaseError):
                unkeyed.execute("SELECT count(*) FROM messages").fetchone()
        chamber.spawn("opener", "keyed-direct", raw=True)
        assert chamber.reap("keyed-direct") == 0, chamber.stderr("keyed-direct")
        chamber.spawn_cli("public-cli", "sessions", "list")
        assert chamber.reap("public-cli") == 0, chamber.stderr("public-cli")
        assert integrity_rows(chamber.db) == ["ok"]
    finally:
        chamber.shutdown()
