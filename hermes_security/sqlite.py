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


def _rebase_sqlcipher_exceptions() -> None:
    """Make sqlcipher3's exception classes catchable as stdlib sqlite3 errors.

    Upstream's state layer catches ``sqlite3.OperationalError`` /
    ``sqlite3.DatabaseError`` in dozens of places; sqlcipher3 raises its own
    parallel hierarchy, so a SQLCipher connection's "no such table" during
    schema init would propagate as an unknown exception type.  Rebasing the
    *Error classes onto their stdlib counterparts (same shape, C layout
    compatible) keeps every existing except-clause working.  ``Error`` and
    ``Warning`` cannot be rebased (immutable layout) and are left as-is.
    """

    import sqlite3

    from sqlcipher3 import dbapi2 as sqlcipher

    for name in (
        "InterfaceError",
        "DatabaseError",
        "DataError",
        "OperationalError",
        "IntegrityError",
        "InternalError",
        "ProgrammingError",
        "NotSupportedError",
    ):
        sc_exc = getattr(sqlcipher, name, None)
        std_exc = getattr(sqlite3, name, None)
        if sc_exc is None or std_exc is None or issubclass(sc_exc, std_exc):
            continue
        try:
            sc_exc.__bases__ = (std_exc,)
        except TypeError:  # pragma: no cover - layout incompatibility
            pass


def _sqlcipher_module() -> Any:
    global _MODULE
    if _MODULE is not None:
        return _MODULE
    try:
        from sqlcipher3 import dbapi2 as sqlcipher
    except ImportError as exc:
        raise SQLCipherUnavailableError(
            "SQLCipher is required; this Hermes fork refuses to create plaintext "
            "SQLite state. Install sqlcipher3 (macOS/Windows) or sqlcipher3-binary (Linux x86_64)."
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
    _rebase_sqlcipher_exceptions()
    _MODULE = sqlcipher
    return sqlcipher


def connect(db_path: Path | str, *, purpose: str = _PURPOSE, key_path: Path | str | None = None, **kwargs: Any):
    """Open (creating if needed) a SQLCipher database keyed by the vault.

    The key is bound to the database's path relative to its vault home, so
    one database's key cannot open another's file even under a different
    profile.  ``key_path`` overrides the path the key is derived from (the
    migration staging file is keyed for its FINAL name).  The key is applied
    and verified with an immediate schema read so a wrong key fails HERE, at
    connect time, not later on first use.
    """

    target = _materialize(db_path)
    home = find_vault_home(target)
    vault = get_vault(home)
    key_source = _materialize(key_path) if key_path is not None else target
    try:
        rel = key_source.resolve(strict=False).relative_to(home).as_posix()
    except ValueError:
        rel = key_source.name  # outside a known home: still filename-bound
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


def _materialize(db_path: Path | str) -> Path:
    """Strip a possible ``file:`` URI down to a real filesystem path."""

    text = str(db_path)
    if text.startswith("file:"):
        text = text[5:]
        text = text.split("?", 1)[0]
    return Path(text).expanduser()


def is_vaulted(db_path: Path | str) -> bool:
    """True when *db_path* lives inside a directory tree with vault metadata."""

    from hermes_security.vault import vault_meta_path

    try:
        return vault_meta_path(find_vault_home(_materialize(db_path))).is_file()
    except Exception:
        return False


def connection_class():
    """SQLCipher's Connection class (factory chains must derive from it)."""

    return _sqlcipher_module().Connection


def row_class():
    """SQLCipher's Row class (string-indexable like stdlib sqlite3.Row)."""

    return _sqlcipher_module().Row


def maybe_connect(db_path: Path | str, **kwargs: Any):
    """Drop-in ``sqlite3.connect`` replacement.

    SQLCipher when *db_path* is inside a vaulted home (encrypted at rest,
    wrong key fails at connect); standard sqlite3 otherwise, so user-owned
    external databases keep their plain format and remain readable by the
    user's own tools.
    """

    if is_vaulted(db_path):
        return connect(db_path, **kwargs)
    import sqlite3 as _stdlib

    return _stdlib.connect(str(db_path), **kwargs)


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
