"""One-shot migration of an existing plaintext Hermes home onto the vault.

``migrate_home`` converts every state file in place:

- SQLite databases -> SQLCipher (``sqlcipher_export``), keys derived from the
  new vault; WAL/journal sidecars checkpointed and removed first.
- Text/config/JSON/YAML/markdown -> AES-GCM envelopes.
- Append-only logs and ``.jsonl`` transcripts -> encrypted frame streams.

Before touching anything it writes a full tar backup OUTSIDE the home
(sibling of the home directory).  Every converted file is verified
(byte-compare for envelopes, full ``iterdump`` compare for databases) before
the plaintext original is replaced.  A final scan asserts no plaintext state
remains inside the home.

Code trees (the git checkout, venvs, uv cache) and installed skill/plugin
code stay public: they are installable artifacts with no user content.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import re

# SQLite identifiers come from sqlite_master; validate before f-string use
# so the fingerprint query builder can never interpolate a crafted name.
_SAFE_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

from hermes_security import errors as vault_errors
from hermes_security import frames as sec_frames
from hermes_security import vault as vault_mod

_MAGIC = b"HRMVAULT\x00"

# Public code/artifact trees inside the home: no user content, regenerable or
# installable artifacts.
_SKIP_DIRS = frozenset(
    {"hermes-agent", "uv", "venv", ".venv", "node_modules", "__pycache__",
     "skills", "optional-skills", "plugins", "pets", "skins", "tui-widgets",
     "desktop-plugins", ".git", "graphify", "lsp", "bin", "cache",
     "image_cache", "audio_cache"}
)

_DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")
_FRAME_SUFFIXES = (".log", ".jsonl")


@dataclass
class MigrationReport:
    home: Path
    backup_path: Optional[Path] = None
    databases: list[str] = field(default_factory=list)
    envelopes: list[str] = field(default_factory=list)
    frame_streams: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def repair_clobbered_state(home: Path | str) -> dict:
    """Re-seal plaintext state files inside a VAULTED home (fork).

    The clobber scenario: an old-venv process (watchdog-respawned gateway,
    stale --yolo session) rewrote sealed envelopes (.env, config-adjacent
    files) as plaintext AFTER migration. This re-seals every eligible
    plaintext file back into an envelope. Databases are REPORTED, never
    re-sealed here — a plaintext DB inside a vaulted home means the vault
    was bypassed wholesale and needs investigation, not silent wrapping.

    Returns {sealed: [relpaths], skipped_dbs: [relpaths]}.
    """

    home_path = Path(home).expanduser().resolve()
    if not vault_mod.vault_exists(home_path):
        raise vault_errors.VaultNotInitializedError(f"No vault at {home_path}")
    vault = vault_mod.get_vault(home_path, allow_env_unlock=False)

    from hermes_security.io import _MAGIC as _ENVELOPE_MAGIC

    db_suffixes = (".db", ".sqlite", ".sqlite3")
    sealed: list[str] = []
    skipped_dbs: list[str] = []
    for path, rel in _iter_files(home_path):
        try:
            head = path.open("rb").read(max(16, len(_ENVELOPE_MAGIC)))
        except OSError:
            continue
        if head.startswith(_ENVELOPE_MAGIC):
            continue  # already sealed
        if path.suffix in db_suffixes and head.startswith(b"SQLite format 3\x00"):
            skipped_dbs.append(str(rel))
            continue
        try:
            # Canonical purpose per file class — the reader decrypts with the
            # same purpose (it is bound into the AAD), so a repaired file must
            # be sealed exactly as migration would have sealed it.
            vault.write_bytes(path, path.read_bytes(), purpose=_purpose_for(rel))
            sealed.append(str(rel))
        except Exception:
            continue
    return {"sealed": sealed, "skipped_dbs": skipped_dbs}


def _iter_files(home: Path):
    for path in sorted(home.rglob("*")):
        if not path.is_file() and not path.is_symlink():
            continue
        rel = path.relative_to(home)
        parts = rel.parts
        if parts and parts[0] in _SKIP_DIRS:
            continue
        if any(part in _SKIP_DIRS for part in parts):
            continue
        if path.name == ".hermes-vault" or path.name.startswith(".hermes-vault."):
            continue
        if path.name.endswith(".lock"):
            continue  # advisory flock sidecars, no content
        yield path, rel


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _backup_home(home: Path, dest_dir: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = dest_dir / f"hermes-premigration-{home.name}-{stamp}.tar"
    with tarfile.open(backup, "w") as tar:
        for path, rel in _iter_files(home):
            tar.add(str(path), arcname=str(rel), recursive=False)
    return backup


def _checkpoint_plain_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    except sqlite3.DatabaseError:
        pass
    finally:
        conn.close()


def _conn_fingerprint(conn) -> str:
    """Schema + all rows, independent of driver (sqlcipher3 has no iterdump).
    Streams rows (a multi-GB state.db must not be materialized)."""

    # Change detection only, never a security primitive — but use a
    # non-crypto-documented digest so scanners and auditors do not have to
    # re-litigate MD5's role here on every pass.
    h = hashlib.sha256()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()]
    for table in tables:
        if not _SAFE_IDENT_RE.fullmatch(table):
            raise ValueError(f"unsafe table name in fingerprint: {table!r}")
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        h.update(f"{table}({','.join(sorted(cols))})".encode())
        rows = []
        for row in conn.execute(f"SELECT * FROM {table}"):
            h.update(repr(row).encode("utf-8", errors="replace"))
            rows.append(None)  # count only
        h.update(f"#{len(rows)}".encode())
    return h.hexdigest()


def _plain_dump_md5(path: Path) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return _conn_fingerprint(conn)
    finally:
        conn.close()


def _export_to_sqlcipher(source: Path, target: Path, hexkey: str) -> None:
    from sqlcipher3 import dbapi2 as sqlcipher

    conn = sqlcipher.connect(str(source))
    try:
        conn.execute(f"ATTACH DATABASE '{target}' AS enc KEY \"x'{hexkey}'\"")
        conn.execute("SELECT sqlcipher_export('enc')")
        conn.execute("DETACH DATABASE enc")
    finally:
        conn.close()


def _migrate_database(home: Path, path: Path, rel: Path, report: MigrationReport) -> None:
    from hermes_security import sqlite as hsql

    vault = vault_mod.get_vault(home)
    hexkey = vault.derive_key(f"sqlcipher:{rel.as_posix()}").hex()

    _checkpoint_plain_db(path)
    for suffix in ("-wal", "-shm", "-journal"):
        side = path.with_name(path.name + suffix)
        if side.exists():
            side.unlink()
    before = _plain_dump_md5(path)

    staging = path.with_name(path.name + ".sqlcipher-new")
    staging.unlink(missing_ok=True)
    try:
        _export_to_sqlcipher(path, staging, hexkey)
        # verify: the staged file must decrypt with the vault key and match the
        # plaintext dump exactly, and stdlib sqlite3 must NOT be able to read it.
        try:
            probe = sqlite3.connect(f"file:{staging}?mode=ro", uri=True)
            probe.execute("SELECT count(*) FROM sqlite_master").fetchone()
            probe.close()
            raise RuntimeError(f"{rel}: staged file is not encrypted (stdlib read it)")
        except sqlite3.DatabaseError:
            pass
        conn = hsql.connect(staging, key_path=path)
        try:
            after = _conn_fingerprint(conn)
        finally:
            conn.close()
        if before != after:
            raise RuntimeError(f"{rel}: dump mismatch after SQLCipher export")
        import os

        os.replace(str(staging), str(path))
        report.databases.append(rel.as_posix())
    finally:
        staging.unlink(missing_ok=True)


def _migrate_envelope(home: Path, path: Path, rel: Path, report: MigrationReport) -> None:
    from hermes_security.io import read_bytes as _plain_check
    from hermes_security.io import write_bytes as _seal_write

    purpose = _purpose_for(rel)
    plaintext = path.read_bytes()
    if plaintext.startswith(_MAGIC):
        report.skipped.append(rel.as_posix() + " (already envelope)")
        return
    _seal_write(path, plaintext, purpose=purpose)
    # verify roundtrip through the vault before declaring done
    from hermes_security.io import read_bytes as _seal_read

    if _seal_read(path, purpose=purpose) != plaintext:
        raise RuntimeError(f"{rel}: envelope roundtrip mismatch")
    report.envelopes.append(rel.as_posix())


def _migrate_frames(home: Path, path: Path, rel: Path, report: MigrationReport) -> None:
    purpose = "log" if path.suffix == ".log" else "transcript"
    plaintext = path.read_bytes()
    if plaintext.startswith(_MAGIC):
        report.skipped.append(rel.as_posix() + " (already sealed)")
        return
    if path.suffix == ".jsonl":
        payloads = [line.encode("utf-8") for line in plaintext.decode("utf-8", errors="replace").splitlines() if line.strip()]
    else:
        payloads = [line.encode("utf-8") for line in plaintext.decode("utf-8", errors="replace").splitlines()]
    if not payloads:
        # empty log: an empty frame stream is the correct sealed form
        import os

        os.chmod(path, 0o600)
        report.frame_streams.append(rel.as_posix())
        return
    tmp = path.with_name(path.name + ".frames-new")
    if tmp.exists():
        tmp.unlink()
    for payload in payloads:
        sec_frames.append(tmp, payload, purpose=purpose)
    back = [p for p in sec_frames.read_frames(tmp, purpose=purpose)]
    if back != payloads:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"{rel}: frame roundtrip mismatch")
    os_replace(tmp, path)
    # drop the migration-time advisory lock sidecar (a fresh one is created on demand)
    path.with_name(path.name + ".lock").unlink(missing_ok=True)
    tmp.with_name(tmp.name + ".lock").unlink(missing_ok=True)
    report.frame_streams.append(rel.as_posix())


def os_replace(src: Path, dst: Path) -> None:
    import os

    os.replace(str(src), str(dst))


def _purpose_for(rel: Path) -> str:
    name = rel.name
    suffix = rel.suffix
    if name == "config.yaml":
        return "config"
    if name == ".env" or name.endswith(".env"):
        return "env"
    if name == "auth.json":
        return "auth"
    if name in ("MEMORY.md", "USER.md") or rel.parts[0] == "memories":
        return "memory"
    if name == "sessions.json":
        return "sessions-index"
    if name == "gateway_state.json":
        return "runtime-status"
    if name == ".update_check":
        return "update-check"
    if rel.parts[0] == "sessions" and suffix == ".json":
        return "transcript"
    return "state"


def _classify(path: Path) -> str:
    # DB sidecars are consumed by the parent DB's migration (checkpointed and
    # removed); migrating one as its own file races the parent conversion.
    if path.name.endswith(("-wal", "-shm", "-journal")):
        return "skip"
    if path.suffix in _DB_SUFFIXES:
        return "db"
    if path.suffix in _FRAME_SUFFIXES:
        return "frames"
    return "envelope"


def migrate_home(home: Path | str, password: str | bytes, *, dry_run: bool = False) -> MigrationReport:
    home = Path(home).expanduser().resolve()
    if vault_mod.vault_exists(home):
        raise vault_errors.VaultError(f"A vault already exists at {home}")
    report = MigrationReport(home=home)

    files = list(_iter_files(home))
    if not files:
        # nothing to convert; still create the vault
        if not dry_run:
            vault_mod.init_vault(home, password, _allow_existing_state=True)
        return report

    backup_root = home.parent
    if not dry_run:
        report.backup_path = _backup_home(home, backup_root)

    # unlock vault FIRST so envelope writers can seal
    if not dry_run:
        vault_mod.init_vault(home, password, _allow_existing_state=True)
        vault_mod.unlock(home, password)

    for path, rel in files:
        try:
            kind = _classify(path)
            if kind == "skip":
                continue
            if kind == "db":
                if dry_run:
                    report.databases.append(rel.as_posix())
                else:
                    _migrate_database(home, path, rel, report)
            elif kind == "frames":
                if dry_run:
                    report.frame_streams.append(rel.as_posix())
                else:
                    _migrate_frames(home, path, rel, report)
            else:
                if dry_run:
                    report.envelopes.append(rel.as_posix())
                else:
                    _migrate_envelope(home, path, rel, report)
        except FileNotFoundError:
            # vanished mid-migration (e.g. a sidecar consumed by its parent
            # DB conversion) — nothing to convert
            report.skipped.append(rel.as_posix() + " (vanished)")
        except Exception as exc:  # noqa: BLE001 - one file must not kill the run
            report.failures.append(f"{rel.as_posix()}: {exc}")

    return report


def scan_for_plaintext(home: Path | str) -> list[str]:
    """Every state file under *home* that is NOT ciphertext (excludes code/skill trees)."""

    home = Path(home).expanduser().resolve()
    offenders = []
    for path, rel in _iter_files(home):
        try:
            head = open(path, "rb").read(16)
        except OSError:
            continue
        if path.suffix in _DB_SUFFIXES:
            if head.startswith(b"SQLite format 3"):
                offenders.append(rel.as_posix())
        elif path.suffix in _FRAME_SUFFIXES:
            # frame streams start with a u32 BE length; heuristic: no plaintext BOM/text
            if head.startswith(b"SQLite") or (head[:4].isascii() and head[:1].isalpha()):
                offenders.append(rel.as_posix())
        else:
            if not head.startswith(_MAGIC):
                offenders.append(rel.as_posix())
    return offenders
