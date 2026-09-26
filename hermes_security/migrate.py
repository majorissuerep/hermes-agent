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
import os
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
    """Bring every state file in a VAULTED home back to its canonical sealed form (fork).

    Each file is judged by its class, exactly as migration classified it —
    never by "does it start with the envelope magic": SQLCipher pages and
    frame streams carry no magic, and wrapping them in an envelope makes them
    unreadable (the first repair did exactly that to every DB and log).

    - databases: SQLCipher left alone; an envelope-wrapped DB is unwrapped
      back to its SQLCipher bytes (verified before the swap); plaintext
      SQLite is REPORTED (vault bypass — investigate, never silently wrap).
    - frame streams (.log/.jsonl): valid frames kept; plaintext appended by a
      non-vault process (whole file or tail) re-framed; envelope-wrapped
      streams unwrapped.
    - everything else: plaintext re-sealed with its canonical purpose. A
      ``.env`` whose content is not text (ciphertext mangled by the old
      NUL-stripping sanitizer) is restored from the newest pre-migration tar.
    - ``hermes-agent.*`` code trees (takeover's rollback copy) that migration
      sealed are restored to plaintext: they are public code, not state.
    """

    home_path = Path(home).expanduser().resolve()
    if not vault_mod.vault_exists(home_path):
        raise vault_errors.VaultNotInitializedError(f"No vault at {home_path}")
    vault = vault_mod.get_vault(home_path, allow_env_unlock=False)

    report: dict = {"sealed": [], "unwrapped": [], "reframed": [], "restored": [],
                    "unrecoverable": [], "skipped_dbs": [], "code_files": 0}
    for path, rel in _iter_files(home_path):
        kind = _classify(path)
        if kind == "skip" or path.is_symlink():
            continue  # never repair through a link (it may point outside the home)
        try:
            with path.open("rb") as handle:
                head = handle.read(16)
            if kind == "db":
                _repair_db(vault, path, rel, head, report)
            elif kind == "frames":
                _repair_frames(vault, path, rel, report)
            else:
                _repair_envelope(vault, home_path, path, rel, head, report)
        except Exception as exc:  # noqa: BLE001 - one file must not stop the sweep
            report["unrecoverable"].append(f"{rel.as_posix()}: {type(exc).__name__}: {exc}")
    report["code_files"] = _restore_code_trees(vault, home_path, report)
    return report


def _frame_purpose(path: Path) -> str:
    return "log" if path.suffix == ".log" else "transcript"


def _repair_db(vault, path: Path, rel: Path, head: bytes, report: dict) -> None:
    if head.startswith(b"SQLite format 3\x00"):
        report["skipped_dbs"].append(rel.as_posix())
        return
    if not head.startswith(_MAGIC):
        return  # SQLCipher: already canonical
    inner = vault.decrypt(path.read_bytes(), purpose=_purpose_for(rel), relpath=rel.as_posix())
    staging = path.with_name(path.name + ".repair-new")
    staging.write_bytes(inner)
    try:
        if not inner.startswith(b"SQLite format 3\x00"):
            from hermes_security import sqlite as hsql

            conn = hsql.connect(staging, key_path=path)
            try:
                conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
            finally:
                conn.close()
        os_replace(staging, path)
    finally:
        staging.unlink(missing_ok=True)
    report["unwrapped"].append(rel.as_posix())
    if inner.startswith(b"SQLite format 3\x00"):
        report["skipped_dbs"].append(rel.as_posix())


def _text_lines(data: bytes, *, jsonl: bool) -> Optional[list[bytes]]:
    """Plaintext log lines, or None when *data* is not text (a binary tail)."""

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if "\x00" in text:
        return None
    return [line.encode("utf-8") for line in text.splitlines() if line.strip() or not jsonl]


def _is_mangled_text(data: bytes) -> bool:
    """Ciphertext pushed through a text sanitizer: the old one decoded with errors=replace and
    stripped NULs, so the result is valid UTF-8 that still starts with the envelope magic and is
    full of U+FFFD."""

    return (
        data.startswith(_MAGIC.rstrip(b"\x00"))
        or "\ufffd".encode("utf-8") in data
        or _text_lines(data, jsonl=False) is None
    )


def _parse_mixed(vault, blob: bytes, *, purpose: str, jsonl: bool) -> Optional[tuple[list[bytes], bool]]:
    """Parse a log that interleaves frames with plaintext lines a non-vault process appended.

    Returns ``(payloads, saw_plaintext)``, or None when a chunk is neither a frame, a text line, nor
    a crash-truncated final frame (a length prefix claiming more bytes than remain — readers
    tolerate it; it is dropped when the stream is rebuilt anyway).
    """

    payloads: list[bytes] = []
    saw_text = False
    offset = 0
    while offset < len(blob):
        frame = sec_frames.decode_frame_at(blob, offset, vault=vault, purpose=purpose)
        if frame is not None:
            payloads.append(frame[0])
            offset = frame[1]
            continue
        newline = blob.find(b"\n", offset)
        end = len(blob) if newline < 0 else newline + 1
        line = _text_lines(blob[offset:end], jsonl=jsonl)
        if line is None:
            remaining = len(blob) - offset
            if remaining < 4 or sec_frames._LENGTH.unpack_from(blob, offset)[0] + 4 > remaining:
                break  # truncated final frame
            return None
        payloads.extend(line)
        saw_text = True
        offset = end
    return payloads, saw_text


def _split_envelope_prefix(vault, blob: bytes, *, relpath: str, purpose: str, frame_purpose: str, jsonl: bool):
    """``(plaintext, trailing_payloads)`` for an envelope that log appends kept growing.

    A log wrapped into an envelope by the old repair stays a log: its handlers keep appending (frames
    from vault-aware processes, plaintext lines from old-venv ones) after the envelope, so the file is
    ``envelope || appends``. The envelope has no length field; the split is the offset whose suffix
    parses to EOF as appends AND whose prefix authenticates.
    """

    minimum = len(_MAGIC) + 1 + 12 + 16
    for offset in range(minimum, len(blob)):
        # Cheap candidate filter before the O(n) checks: a frame starts here, or a text line does.
        if sec_frames.decode_frame_at(blob, offset, vault=vault, purpose=frame_purpose) is None \
                and _text_lines(blob[offset : offset + 16], jsonl=False) is None:
            continue
        parsed = _parse_mixed(vault, blob[offset:], purpose=frame_purpose, jsonl=jsonl)
        if not parsed or not parsed[0]:
            continue
        try:
            return vault.decrypt(blob[:offset], purpose=purpose, relpath=relpath), parsed[0]
        except vault_errors.VaultIntegrityError:
            continue
    raise vault_errors.VaultIntegrityError(f"{relpath}: envelope does not authenticate")


def _repair_frames(vault, path: Path, rel: Path, report: dict) -> None:
    raw = path.read_bytes()
    wrapped = raw.startswith(_MAGIC)
    purpose = _frame_purpose(path)
    jsonl = path.suffix == ".jsonl"
    appended: list[bytes] = []
    source = raw
    if wrapped:
        try:
            source = vault.decrypt(raw, purpose=_purpose_for(rel), relpath=rel.as_posix())
        except vault_errors.VaultIntegrityError:
            source, appended = _split_envelope_prefix(
                vault, raw, relpath=rel.as_posix(), purpose=_purpose_for(rel),
                frame_purpose=purpose, jsonl=jsonl)
    parsed = _parse_mixed(vault, source, purpose=purpose, jsonl=jsonl)
    if parsed is None:
        raise RuntimeError("neither frames nor text (binary content mid-stream); left as is")
    payloads, saw_text = parsed
    if not wrapped and not saw_text:
        return  # clean frame stream (a truncated tail is tolerated by readers)
    payloads.extend(appended)
    rebuilt = sec_frames.encode_frames(payloads, vault=vault, purpose=purpose)
    if sec_frames.split_stream(rebuilt, vault=vault, purpose=purpose)[0] != payloads:
        raise RuntimeError("frame roundtrip mismatch")
    vault_mod._atomic_write(path, rebuilt)
    report["reframed" if saw_text or appended else "unwrapped"].append(rel.as_posix())


def _repair_envelope(vault, home: Path, path: Path, rel: Path, head: bytes, report: dict) -> None:
    purpose = _purpose_for(rel)
    if head.startswith(_MAGIC):
        if purpose != "env":
            return  # sealed; only .env content is sanity-checked (it gates every command)
        content = vault.decrypt(path.read_bytes(), purpose=purpose, relpath=rel.as_posix())
    else:
        content = path.read_bytes()
    if purpose == "env" and _is_mangled_text(content):
        _restore_env_from_backup(vault, home, path, rel, content, report)
        return
    if head.startswith(_MAGIC):
        return
    vault.write_bytes(path, content, purpose=purpose)
    report["sealed"].append(rel.as_posix())


def _restore_env_from_backup(vault, home: Path, path: Path, rel: Path, garbage: bytes, report: dict) -> None:
    """A mangled .env is not recoverable from itself; the pre-migration tar
    (written OUTSIDE the home by migrate_home) holds the last plaintext copy."""

    member = rel.as_posix()
    for tar_path in sorted(home.parent.glob(f"hermes-premigration-{home.name}-*.tar"), reverse=True):
        try:
            with tarfile.open(tar_path) as tar:
                extracted = tar.extractfile(member)
                original = extracted.read() if extracted is not None else None
        except (KeyError, OSError, tarfile.TarError):
            continue
        if original is None or _is_mangled_text(original):
            continue
        # Keep the mangled bytes (sealed) instead of destroying the evidence.
        vault.write_bytes(path.with_name(path.name + ".clobbered"), garbage, purpose="state")
        vault.write_bytes(path, original, purpose=_purpose_for(rel))
        report["restored"].append(f"{member} (from {tar_path})")
        return
    report["unrecoverable"].append(
        f"{member}: content is not text (mangled ciphertext) and no pre-migration "
        f"backup in {home.parent} holds it — re-enter these secrets"
    )


def _restore_code_trees(vault, home: Path, report: dict) -> int:
    """Unseal public ``hermes-agent.*`` code trees that migration wrongly sealed."""

    restored = 0
    for tree in sorted(home.glob("hermes-agent.*")):
        if not tree.is_dir() or tree.is_symlink():
            continue
        for dirpath, dirnames, filenames in os.walk(tree):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for name in filenames:
                path = Path(dirpath) / name
                if path.is_symlink():
                    continue
                rel = path.relative_to(home)
                try:
                    raw = path.read_bytes()
                    data = raw
                    if raw.startswith(_MAGIC):
                        data = vault.decrypt(raw, purpose=_purpose_for(rel), relpath=rel.as_posix())
                    if path.suffix in _FRAME_SUFFIXES and data:
                        payloads, consumed = sec_frames.split_stream(data, vault=vault, purpose=_frame_purpose(path))
                        if payloads and consumed == len(data):
                            data = b"\n".join(payloads) + b"\n"
                    if data != raw:
                        vault_mod._atomic_write(path, data, mode=path.stat().st_mode & 0o777)
                        restored += 1
                except Exception as exc:  # noqa: BLE001
                    report["unrecoverable"].append(f"{rel.as_posix()}: {type(exc).__name__}: {exc}")
    return restored


def _iter_files(home: Path, *, onerror=None):
    def failed(exc):
        if onerror is None:
            # A first-run home has nothing to migrate yet. Scanners still report it.
            if isinstance(exc, FileNotFoundError) and exc.filename == str(home):
                return
            raise exc
        onerror(exc)

    # rglob suppresses directory-read failures, falsely certifying incomplete scans.
    for root, directories, files in os.walk(home, onerror=failed):
        parent = Path(root)
        links = [name for name in directories if (parent / name).is_symlink()]
        directories[:] = sorted(name for name in directories if name not in links
                                and not vault_mod._is_in_skip_dir((parent / name).relative_to(home)))
        for name in sorted(files + links):
            path = parent / name
            if not path.is_file() and not path.is_symlink():
                continue
            rel = path.relative_to(home)
            if vault_mod._is_in_skip_dir(rel):
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
    fd, name = tempfile.mkstemp(prefix=f"hermes-premigration-{home.name}-{stamp}-",
                                suffix=".tar", dir=dest_dir)
    backup = Path(name)
    # mkstemp is owner-only from creation, not after the sensitive first write.
    with os.fdopen(fd, "wb") as handle:
        with tarfile.open(fileobj=handle, mode="w") as tar:
            for path, rel in _iter_files(home):
                tar.add(str(path), arcname=str(rel), recursive=False)
        handle.flush()
        os.fsync(handle.fileno())
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
    for pragma in ("user_version", "application_id"):
        h.update(repr((pragma, conn.execute(f"PRAGMA {pragma}").fetchone()[0])).encode())
    for row in conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name"
    ):
        h.update(repr(row).encode("utf-8"))
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
        conn.execute(f"ATTACH DATABASE ? AS enc KEY \"x'{hexkey}'\"", (str(target),))
        conn.execute("SELECT sqlcipher_export('enc')")
        # sqlcipher_export deliberately excludes these application-owned header fields.
        for pragma in ("user_version", "application_id"):
            value = int(conn.execute(f"PRAGMA main.{pragma}").fetchone()[0])
            conn.execute(f"PRAGMA enc.{pragma} = {value}")
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
    """State paths whose encryption cannot be verified (including unreadable/locked state).

    Code, skills and cache roots retain the migration exclusion policy. An empty
    result qualifies only those inspected paths, not excluded trees.
    """
    from hermes_security import io as state_io
    from hermes_security import sqlite as state_sqlite

    home = Path(home).expanduser().resolve()
    offenders = []
    def unreadable(exc):
        offenders.append(Path(exc.filename or home).relative_to(home).as_posix())

    for path, rel in _iter_files(home, onerror=unreadable):
        try:
            if path.suffix in _DB_SUFFIXES:
                owner = vault_mod.find_vault_home(path)
                vault = vault_mod.get_vault(owner)
                key = vault.derive_key(f"sqlcipher:{path.relative_to(owner).as_posix()}").hex()
                # Inspection must not create a database or modify journal settings.
                conn = state_sqlite._sqlcipher_module().connect(path.as_uri() + "?mode=ro", uri=True)
                try:
                    conn.execute(f"PRAGMA key = \"x'{key}'\"")
                    valid = conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
                finally:
                    conn.close()
            elif path.suffix in _FRAME_SUFFIXES:
                raw = path.read_bytes()
                vault = vault_mod.get_vault(vault_mod.find_vault_home(path))
                _, consumed = sec_frames.split_stream(raw, vault=vault, purpose=_frame_purpose(path))
                valid = consumed == len(raw)
            else:
                owner = state_io._home_for(path)
                if owner is None:
                    valid = False
                elif path.parent == owner / "backups" / "config" and path.name.startswith("config.yaml."):
                    from hermes_cli.config_backups import _read_backup_bytes
                    # The backup reader supports current and historical authenticated AADs.
                    _read_backup_bytes(path)
                    valid = True
                else:
                    valid = state_io.read_bytes(path, purpose=_purpose_for(path.relative_to(owner))) is not None
        except (OSError, ValueError, vault_errors.VaultError, sqlite3.DatabaseError):
            valid = False
        if not valid:
            offenders.append(rel.as_posix())
    return offenders
