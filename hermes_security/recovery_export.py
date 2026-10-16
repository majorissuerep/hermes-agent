"""Verified plaintext recovery archives; never decrypt the live home in place."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile

from hermes_security import frames, vault as vault_mod
from hermes_security.errors import VaultIntegrityError
from hermes_security.migrate import _DB_SUFFIXES, _FRAME_SUFFIXES, _conn_fingerprint, _frame_purpose, _purpose_for

_MANIFEST = ".hermes-export.json"


def _export_database(source: Path, destination: Path, vault) -> None:
    """SQLCipher exports a read transaction, including committed WAL and header metadata."""
    from hermes_security.sqlite import _sqlcipher_module

    destination.touch(mode=0o600, exist_ok=False)
    connection = _sqlcipher_module().connect(source.as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        key = vault.derive_key("sqlcipher:" + source.relative_to(vault.home).as_posix()).hex()
        connection.execute(f"PRAGMA key = \"x'{key}'\"")
        connection.execute("ATTACH DATABASE ? AS exported KEY ''", (str(destination),))
        connection.execute("BEGIN")
        if connection.execute("PRAGMA main.integrity_check").fetchall() != [("ok",)]:
            raise VaultIntegrityError(f"Database integrity check failed: {source.name}")
        expected = _conn_fingerprint(connection)
        connection.execute("SELECT sqlcipher_export('exported')")
        for pragma in ("user_version", "application_id"):
            value = int(connection.execute(f"PRAGMA main.{pragma}").fetchone()[0])
            connection.execute(f"PRAGMA exported.{pragma} = {value}")
        connection.commit()
        connection.execute("DETACH DATABASE exported")
    finally:
        connection.close()
    with sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True) as plain:
        if plain.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or _conn_fingerprint(plain) != expected:
            raise VaultIntegrityError(f"Decrypted database verification failed: {source.name}")


def _plaintext(path: Path, relative: Path, vault) -> bytes:
    raw = path.read_bytes()
    if raw.startswith(b"HRMVAULT\x00"):
        return vault.decrypt(raw, purpose=_purpose_for(relative), relpath=relative.as_posix())
    if path.suffix in _FRAME_SUFFIXES and not vault_mod._is_in_skip_dir(relative):
        payloads, consumed = frames.split_stream(raw, vault=vault, purpose=_frame_purpose(path), strict=True)
        if consumed != len(raw):
            raise VaultIntegrityError(f"Incomplete stream {relative}; run secure-vault repair first")
        return b"".join(payload + b"\n" for payload in payloads)
    if not vault_mod._is_in_skip_dir(relative):
        raise VaultIntegrityError(f"Unsealed state {relative}; run secure-vault repair first")
    return raw  # user skills/plugins and other deliberately public artifacts


def _add_bytes(archive: tarfile.TarFile, name: str, data: bytes, *, executable: bool = False) -> str:
    entry = tarfile.TarInfo(name)
    entry.size = len(data)
    entry.mode = 0o700 if executable else 0o600
    archive.addfile(entry, io.BytesIO(data))
    return hashlib.sha256(data).hexdigest()


def _raise_walk_error(error: OSError) -> None:
    raise error


def _files(home: Path):
    """Use the established full-backup exclusion policy, without swallowing walk errors."""
    from hermes_cli.backup import _IMPORT_SKIP_NAMES, _in_excluded_root_dir, _should_exclude

    for root, directories, filenames in os.walk(home, onerror=_raise_walk_error, followlinks=False):
        parent = Path(root)
        relative_parent = parent.relative_to(home)
        directories[:] = sorted(name for name in directories
                                if not (parent / name).is_symlink()
                                and not _should_exclude(relative_parent / name)
                                and not _in_excluded_root_dir(relative_parent / name)
                                and not (relative_parent == Path(".") and
                                         (name == "profiles" or name.startswith("hermes-agent."))))
        for name in sorted(filenames):
            path = parent / name
            relative = path.relative_to(home)
            if (path.is_symlink() or not path.is_file()
                    or _should_exclude(relative) or _in_excluded_root_dir(relative)
                    or name in _IMPORT_SKIP_NAMES or relative == Path("vault.key")
                    or name.startswith(".hermes-vault")
                    or (name.endswith(".lock") and path.stat().st_size == 0)):
                continue
            if name == _MANIFEST:
                continue  # a prior export's inventory is replaced, not nested
            yield path, relative


def export_home(home: Path | str, output: Path | str) -> dict:
    """Publish an owner-only tar only after all selected state has authenticated and verified.

    Exclusions match full backups (code, caches, old backups, symlinks, runtime
    sidecars), plus master unlock material/metadata and other profiles. Export profiles
    independently. No source byte is rewritten; failed staging is removed.
    """
    from hermes_security.io import _home_for

    home = Path(home).expanduser().resolve(strict=True)
    output = Path(output).expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Export destination already exists: {output}")
    output = output.resolve(strict=False)
    if output == home or home in output.parents or _home_for(output) is not None:
        raise ValueError("Plaintext export must be outside every encrypted Hermes home")
    vault = vault_mod.get_vault(home, allow_env_unlock=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    hashes = {}
    databases = 0
    with tempfile.TemporaryDirectory(prefix=".hermes-export-", dir=output.parent) as directory:
        staging = Path(directory)
        packed = staging / "recovery.tar"
        with packed.open("xb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            with tarfile.open(fileobj=handle, mode="w") as archive:
                for path, relative in _files(home):
                    if path.suffix in _DB_SUFFIXES and not vault_mod._is_in_skip_dir(relative):
                        database = staging / "database.sqlite"
                        _export_database(path, database, vault)
                        data = database.read_bytes()
                        database.unlink()
                        databases += 1
                    else:
                        data = _plaintext(path, relative, vault)
                    hashes[relative.as_posix()] = _add_bytes(
                        archive, relative.as_posix(), data, executable=bool(path.stat().st_mode & 0o100))
                manifest = json.dumps({"format": 1, "sha256": hashes}, sort_keys=True).encode() + b"\n"
                _add_bytes(archive, _MANIFEST, manifest)
            handle.flush()
            os.fsync(handle.fileno())
        # Same-filesystem hard-link publication is atomic and refuses a concurrent
        # destination, unlike replace/rename which could clobber another export.
        os.link(packed, output)
    return {"files": len(hashes), "databases": databases}
