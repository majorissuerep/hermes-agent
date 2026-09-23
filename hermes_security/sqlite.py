"""Mandatory SQLCipher connection factory for Hermes-owned databases.

Every SQLite database under the Hermes home is a SQLCipher database keyed
with a vault-derived key bound to the database's path relative to its vault
home.  Standard sqlite3 cannot open these files; there is no plaintext
fallback and no bypass.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from hermes_security.errors import VaultError
from hermes_security.vault import find_vault_home, get_vault

_PURPOSE = "sqlcipher"
_MODULE: Any = None


class SQLCipherUnavailableError(VaultError):
    """The runtime does not provide SQLCipher; Hermes refuses plaintext state."""


def _sqlcipher_module() -> Any:
    global _MODULE
    if _MODULE is not None:
        return _MODULE
    try:
        from sqlcipher3 import dbapi2 as sqlcipher
    except ImportError as exc:
        raise SQLCipherUnavailableError(
            "SQLCipher is required; this Hermes fork refuses to create plaintext "
            "SQLite state. Install sqlcipher3-binary."
        ) from exc
    # Prove the runtime really has SQLCipher: the pragma must exist.
    probe = sqlcipher.connect(":memory:")
    try:
        probe.execute("PRAGMA cipher_version")
    except sqlite3.DatabaseError as exc:
        raise SQLCipherUnavailableError(
            "The SQLite runtime does not provide SQLCipher"
        ) from exc
    finally:
        probe.close()
    _MODULE = sqlcipher
    return sqlcipher


def connect(db_path: Path | str, *, purpose: str = _PURPOSE, **kwargs: Any):
    """Open (creating if needed) a SQLCipher database keyed by the vault.

    The key is bound to the database's path relative to its vault home, so
    one database's key cannot open another's file even under a different
    profile.  The key is applied and verified with an immediate schema read
    so a wrong key fails HERE, at connect time, not later on first use.
    """

    target = Path(db_path).expanduser()
    home = find_vault_home(target)
    vault = get_vault(home)
    try:
        rel = target.resolve(strict=False).relative_to(home).as_posix()
    except ValueError:
        rel = target.name  # outside a known home: still filename-bound
    key = vault.derive_key(f"{purpose}:{rel}")
    hexkey = key.hex()

    sqlcipher = _sqlcipher_module()
    conn = sqlcipher.connect(str(target), **kwargs)
    try:
        conn.execute(f"PRAGMA key = \"x'{hexkey}'\"")
        # Force a schema read: wrong keys fail now, not on first query.
        conn.execute("SELECT count(*) FROM sqlite_master")
    except BaseException:
        conn.close()
        raise
    return conn


# Re-export the DB-API exception types so callers can catch them uniformly.
try:
    from sqlcipher3 import dbapi2 as _driver

    DatabaseError = _driver.DatabaseError
    OperationalError = _driver.OperationalError
    IntegrityError = _driver.IntegrityError
    Row = _driver.Row
except ImportError:  # pragma: no cover - surfaced on first connect() instead
    DatabaseError = sqlite3.DatabaseError
    OperationalError = sqlite3.OperationalError
    IntegrityError = sqlite3.IntegrityError
    Row = sqlite3.Row
