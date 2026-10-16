"""Read historical snapshot metadata and rebind pre-vault payloads for restore.

Early whole-home migrations sealed snapshot files at their storage paths. The
live readers need the destination paths' keys/AAD instead. Normalize a private
copy during restore; never rewrite the saved recovery point.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import tempfile

from hermes_security import io as state_io, vault as vault_mod
from hermes_security.migrate import _DB_SUFFIXES, _FRAME_SUFFIXES, _purpose_for


def read_snapshot_manifest(path: Path) -> dict:
    raw = path.read_bytes()
    if raw.startswith(b"HRMVAULT\x00"):
        raw = state_io.read_bytes(path, purpose="state")
    value = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("snapshot manifest is not an object")
    return value


def _legacy_snapshot_home(source: Path) -> Path | None:
    home = state_io._home_for(source)
    if home is None:
        return None
    relative = source.resolve().relative_to(home)
    if len(relative.parts) < 3 or relative.parts[0] != "state-snapshots":
        return None
    manifest = home / relative.parts[0] / relative.parts[1] / "manifest.json"
    with manifest.open("rb") as handle:
        return home if handle.read(9) == b"HRMVAULT\x00" else None


@contextmanager
def snapshot_source(source: Path, destination: Path):
    """Yield the original canonical payload or a verified destination-keyed copy."""
    source_home = _legacy_snapshot_home(source)
    destination_home = state_io._home_for(destination)
    if source_home is None or destination_home is None:
        yield source
        return
    logical = destination.resolve().relative_to(destination_home)
    if vault_mod._is_in_skip_dir(logical) or source.suffix in _FRAME_SUFFIXES:
        yield source
        return
    with tempfile.TemporaryDirectory(prefix="hermes-snapshot-recovery-") as directory:
        prepared = Path(directory) / source.name
        if source.suffix in _DB_SUFFIXES:
            from hermes_cli.backup_sqlite import _safe_copy_db

            if not _safe_copy_db(source, prepared, key_path=destination, source_key_path=source):
                raise ValueError("legacy snapshot database could not be rebound to its destination")
        else:
            raw = source.read_bytes()
            source_relative = source.resolve().relative_to(source_home)
            source_vault = vault_mod.get_vault(source_home)
            if raw.startswith(b"HRMVAULT\x00"):
                raw = source_vault.decrypt(raw, purpose=_purpose_for(source_relative),
                                           relpath=source_relative.as_posix())
            destination_vault = vault_mod.get_vault(destination_home)
            canonical = destination_vault.encrypt(raw, purpose=_purpose_for(logical), relpath=logical.as_posix())
            vault_mod._atomic_write(prepared, canonical)
        yield prepared
