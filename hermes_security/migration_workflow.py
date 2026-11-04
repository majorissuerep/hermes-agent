"""Credential-first encrypted rollback and explicit, class-aware migration continuation."""
from __future__ import annotations

import os
import json
import sqlite3
from pathlib import Path
import secrets
import tempfile
import time

from hermes_security import migration_state, vault
from hermes_security.errors import VaultError, VaultIntegrityError


def _backup_directory(home):
    from hermes_security.migrate import _private_backup_directory
    from hermes_security.migration_archive import MigrationArchiveError, _destination_parent
    from hermes_security.migration_profiles import storage_home

    owner = storage_home(home)
    for directory in (owner.parent, _private_backup_directory(owner)):
        if directory is None:
            continue
        try:
            with _destination_parent(directory), tempfile.TemporaryFile(dir=directory):
                return directory
        except (PermissionError, MigrationArchiveError):
            continue
    raise PermissionError("Cannot create encrypted rollback; provide an owned 0700 sibling migration-backups directory")


def entries(home):
    from hermes_security.migration_profiles import snapshot_entries

    yield from snapshot_entries(home)


def _backup(home, state, unlocked):
    from hermes_security.artifacts import _Custody
    from hermes_security.migration_archive import verified_tar, write_backup
    from hermes_security.migration_resume import file_hash

    if state["backup"] is None:
        directory = _backup_directory(home)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        state["backup"] = str(directory / f"hermes-premigration-{home.name}-{stamp}-{state['id']}.hmb")
        migration_state.write(home, unlocked, state)
    target = Path(state["backup"])
    from hermes_security.migration_profiles import storage_home

    owner = storage_home(home)
    permitted = {owner.parent, owner.parent / (owner.name + "-migration-backups"),
                 home.parent, home.parent / (home.name + "-migration-backups")}
    if target.parent not in permitted or target.is_symlink():
        raise VaultIntegrityError("Migration rollback path does not belong to the original home")
    custody = _Custody(home)
    if target.exists():
        # A crash after publication may precede checkpoint advancement. Do not overwrite it.
        metadata = _archive_metadata(target, home, unlocked, state)
        with verified_tar(target, custody=custody, expected_metadata=metadata):
            pass
        with target.open("rb") as handle:
            os.fsync(handle.fileno())
        vault._fsync_directory(target.parent)
    else:
        write_backup(home, target, custody=custody, entries=entries(home),
                     metadata=(home / ".hermes-vault").read_bytes())
    state.update(phase="converting", sha256=file_hash(target))
    migration_state.write(home, unlocked, state)
    return target


def _convert_member(home, member, unlocked, state, report):
    from hermes_cli.archive_safe import normalize_archive_parts
    from hermes_security import migrate
    from hermes_security.migration_resume import admitted

    parts = normalize_archive_parts(member.name)
    relative = Path(*parts)
    target = home / relative
    if not target.resolve().is_relative_to(home):
        raise VaultIntegrityError("Migration rollback member escaped its original home")
    original = {name.removeprefix("HERMES."): value for name, value in member.pax_headers.items()
                if name.startswith("HERMES.")}
    kind = original.get("kind")
    if kind in {"skip", "profile"}:
        return  # full rollback retains independent islands without using their keys
    if kind == "browser":
        migrate._note_browser_root(report, relative)
        return
    if kind not in {"db", "frames", "envelope"}:
        raise VaultIntegrityError("Migration rollback member lacks its original classification")
    if admitted(target, relative, original, unlocked, checkpoint=state["current"]):
        report.skipped.append(relative.as_posix() + " (verified converted content)")
        return
    state["current"] = {"path": relative.as_posix(), "kind": kind,
                        "original": original.get("sha256")}
    migration_state.write(home, unlocked, state)
    migrate._harden_private_artifact(home, relative)
    converters = {"db": migrate._migrate_database, "frames": migrate._migrate_frames,
                  "envelope": migrate._migrate_envelope}
    converters[kind](home, target, relative, report)
    if not admitted(target, relative, original, unlocked, checkpoint=state["current"]) and target.stat().st_size:
        raise VaultIntegrityError("Migration conversion did not produce authenticated state")
    state["current"] = None
    migration_state.write(home, unlocked, state)


def _continue(home, unlocked, state, report):
    from hermes_security import migrate
    from hermes_security.artifacts import _Custody
    from hermes_security.migration_archive import verified_tar
    from hermes_security.migration_resume import file_hash

    target = _backup(home, state, unlocked) if state["phase"] == "preparing" else Path(state["backup"])
    report.backup_path = target
    if file_hash(target) != state["sha256"]:
        raise VaultIntegrityError("Verified migration rollback changed; no source files were converted")
    metadata = _archive_metadata(target, home, unlocked, state)
    with verified_tar(target, custody=_Custody(home), expected_sha256=state["sha256"],
                      expected_metadata=metadata) as archive:
        for member in archive:
            try:
                _convert_member(home, member, unlocked, state, report)
            except (OSError, ValueError, VaultError, RuntimeError, sqlite3.Error) as error:
                report.failures.append(f"{member.name}: {type(error).__name__}: {error}")
                break  # retain one exact interrupted entry; do not create new conversion debt
    if not report.failures:
        migrate._convert_browser_profiles(home, report)
    if not report.failures:
        leftovers = migrate.scan_for_plaintext(home, selected_only=True)
        if leftovers:
            report.failures.append("Unconverted state remains; retain the encrypted rollback and run migrate again")
        else:
            migration_state.write(home, unlocked, None)
    return report


def _plan(home):
    from hermes_security import migrate

    report = migrate.MigrationReport(home=home)
    from hermes_security.migration_profiles import refuse_ancestor_sealed, scope

    refuse_ancestor_sealed(home)
    report.profiles_opaque, report.profiles_pending = scope(home)
    destinations = {"db": report.databases, "frames": report.frame_streams,
                    "envelope": report.envelopes}
    for path, relative in migrate._iter_files(home, exclude_roots={"profiles"}):
        kind = migrate._classify(path, relative)
        if kind == "browser":
            migrate._note_browser_root(report, relative)
        elif kind in destinations:
            destinations[kind].append(relative.as_posix())
    return report


def run(home, password, *, dry_run=False, public_key_raw=None):
    from hermes_security import migrate
    from hermes_state.runtime_ownership import exclusive_maintenance
    from hermes_security.migration_profiles import children, refuse_ancestor_sealed, scope

    home = Path(home).expanduser().resolve()
    if dry_run:
        if vault.vault_exists(home):
            raise VaultError("An existing vault requires explicit authenticated migration continuation")
        return _plan(home)
    with exclusive_maintenance([home, *children(home)]):
        if vault.vault_exists(home):
            if not migration_state.pending(home):
                raise VaultError("A vault already exists without an authenticated pending migration")
            unlocked = vault.get_vault(home, allow_env_unlock=False) if password is None else vault.unlock(home, password)
        else:
            refuse_ancestor_sealed(home)
            has_state = next(entries(home), None) is not None
            if not has_state:
                vault.init_vault(home, password, _allow_existing_state=True, public_key_raw=public_key_raw)
                report = migrate.MigrationReport(home=home)
                report.profiles_opaque, report.profiles_pending = scope(home)
                return report
            _backup_directory(home)  # refuse unavailable rollback storage before publishing credential metadata
            vault.init_vault(home, password, _allow_existing_state=True, public_key_raw=public_key_raw,
                             _migration_id=secrets.token_hex(16))
            unlocked = vault.unlock(home, password)
        state = migration_state.read(home, unlocked)
        report = migrate.MigrationReport(home=home)
        report.profiles_opaque, report.profiles_pending = scope(home)
        return _continue(home, unlocked, state, report)


def original_member(backup: Path, home: Path, name: str) -> bytes | None:
    """Passive legacy reader plus complete-authentication-first encrypted repair input."""
    import tarfile
    from hermes_security.artifacts import _Custody
    from hermes_security.migration_archive import verified_tar

    context = (verified_tar(backup, custody=_Custody(home)) if backup.suffix == ".hmb"
               else tarfile.open(backup))
    with context as archive:
        for member in archive:
            if member.name == name and member.isfile():
                stream = archive.extractfile(member)
                return stream.read() if stream is not None else None
    return None


def _archive_metadata(target, home, unlocked, state):
    from hermes_security.migration_archive import read_backup_metadata

    metadata = read_backup_metadata(target)
    original = migration_state.decode(json.loads(metadata), home, unlocked)
    if original["id"] != state["id"] or original.get("backup") != str(target):
        raise VaultIntegrityError("Migration rollback belongs to a different checkpoint")
    return metadata
