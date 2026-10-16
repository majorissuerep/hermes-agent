"""Read retained recovery archives and seal legacy payloads before atomic publication."""
from __future__ import annotations

from contextlib import closing, contextmanager
import io
import json
import os
from pathlib import Path
import sqlite3
import zipfile


@contextmanager
def open_backup_zip(path: Path, home: Path):
    from hermes_security.io import read_bytes
    from hermes_cli.backup_restore import _detect_prefix

    with path.open("rb") as handle:
        sealed = handle.read(9) == b"HRMVAULT\0"
    source = io.BytesIO(read_bytes(path, purpose="state")) if sealed else path
    if not zipfile.is_zipfile(source):
        print(f"Error: Not a valid zip file: {path}")
        raise SystemExit(1)
    with zipfile.ZipFile(source, "r") as archive:
        prefix = _detect_prefix(archive)
        for member in archive.namelist():
            relative = member.removeprefix(prefix)
            if Path(relative).name != ".hermes-vault":
                continue
            from hermes_cli.archive_safe import normalize_archive_parts

            target = home.joinpath(*normalize_archive_parts(relative))
            if not target.resolve().is_relative_to(home.resolve()):
                raise OSError("archive vault metadata escapes the selected home")
            if not target.is_file() or archive.read(member) != target.read_bytes():
                raise SystemExit("Error: Backup vault metadata differs from the selected home; recover an isolated "
                                 "copy with its original vault credential, or migrate a secure-vault export. "
                                 "No files were restored.")
        yield archive


def _seal_database(staged: Path, target: Path, vault, relative: Path) -> None:
    from hermes_security.migrate import _conn_fingerprint, _export_to_sqlcipher
    from hermes_security.sqlite import connect

    with closing(sqlite3.connect(staged.as_uri() + "?mode=ro", uri=True)) as plain:
        expected = _conn_fingerprint(plain)
    encrypted = staged.with_name(staged.name + ".cipher")
    encrypted.touch(mode=0o600, exist_ok=False)
    try:
        _export_to_sqlcipher(staged, encrypted, vault.derive_key("sqlcipher:" + relative.as_posix()).hex())
        with closing(connect(encrypted, key_path=target)) as verified:
            if (verified.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                    or _conn_fingerprint(verified) != expected):
                raise OSError("legacy database conversion did not preserve contents and metadata")
        os.replace(encrypted, staged)
    finally:
        encrypted.unlink(missing_ok=True)


def _seal_frames(raw: bytes, target: Path, vault) -> bytes:
    from hermes_security import frames
    from hermes_security.migrate import _frame_purpose

    purpose = _frame_purpose(target)
    if frames.decode_frame_at(raw, 0, vault=vault, purpose=purpose) is not None:
        frames._complete_stream(raw, vault=vault, purpose=purpose)
        return raw
    text = raw.decode("utf-8")
    if any(ord(character) < 32 and character not in "\n\r\t" for character in text):
        raise ValueError("archive contains an incomplete or unauthenticated frame stream")
    lines = text.splitlines()
    if target.suffix == ".jsonl":
        lines = [line for line in lines if line.strip()]
        for line in lines:
            json.loads(line)
    return frames.encode_frames([line.encode() for line in lines], vault=vault, purpose=purpose)


def prepare_import_file(staged: Path, target: Path) -> None:
    """Normalize only the private staged copy; never publish plaintext into a vaulted home."""
    from hermes_security import io as state_io, vault as vault_mod
    from hermes_security.errors import VaultError
    from hermes_security.migrate import _DB_SUFFIXES, _FRAME_SUFFIXES, _purpose_for

    home = state_io._home_for(target)
    if home is None:
        return
    relative = target.resolve().relative_to(home)
    if (vault_mod._is_in_skip_dir(relative) or target.name.startswith(".hermes-vault")
            or relative == Path("vault.key")):
        return
    vault = vault_mod.get_vault(home)
    try:
        with staged.open("rb") as handle:
            header = handle.read(16)
        if target.suffix in _DB_SUFFIXES:
            if header == b"SQLite format 3\0":
                _seal_database(staged, target, vault, relative)
            from hermes_cli.backup import verify_sqlite_integrity

            checked = verify_sqlite_integrity(staged, key_path=target)
            if not checked["valid"]:
                raise OSError("archived database failed integrity verification: " + checked["message"])
            return
        raw = staged.read_bytes()
        purpose = _purpose_for(relative)
        if raw.startswith(b"HRMVAULT\0"):
            vault.decrypt(raw, purpose=purpose, relpath=relative.as_posix())
            return
        sealed = (_seal_frames(raw, target, vault) if target.suffix in _FRAME_SUFFIXES
                  else vault.encrypt(raw, purpose=purpose, relpath=relative.as_posix()))
        with staged.open("wb") as handle:
            handle.write(sealed)
            handle.flush()
            os.fsync(handle.fileno())
    except (ValueError, VaultError, sqlite3.Error) as exc:
        raise OSError(f"Cannot authenticate/convert {relative}: {exc}") from exc
