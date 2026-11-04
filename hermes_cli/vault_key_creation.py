"""Explicit external credential creation for unattended vault migration."""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import tempfile


class KeyOutputError(ValueError):
    """The requested credential could not be privately published before migration."""


def key_destination(home: Path, output: str | None) -> Path:
    """Admission precedes backup, key generation and all migration writes."""
    from hermes_security.io import _home_for

    if not output:
        raise ValueError("--key-only requires --key-out naming a NEW file outside the Hermes home")
    requested = Path(output).expanduser().absolute()
    if requested.is_symlink() or requested.exists():
        raise ValueError("--key-out must name a new file; existing files and links are never overwritten")
    target = requested.resolve(strict=False)
    if target.is_relative_to(Path(home).expanduser().resolve(strict=False)) or _home_for(target) is not None:
        raise ValueError("--key-out must be outside this home and every encrypted Hermes home")
    return target


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_new_credential(path: Path, data: bytes) -> None:
    """Publish complete private bytes without replacing an existing output."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staging = Path(name)
    published = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(staging, path)
        published = True
        _sync_directory(path.parent)
    except BaseException:
        if published and path.exists() and os.path.samefile(staging, path):
            path.unlink()
        raise
    finally:
        staging.unlink(missing_ok=True)


def migrate_with_key_file(home: Path, output: Path) -> int:
    """Keep the historical external-key mode, with an explicit exclusive output."""
    from hermes_security import migrate, vault

    private_key, public_key = vault.generate_keypair()
    try:
        output = key_destination(home, str(output))
        _write_new_credential(output, vault._b64e(private_key).encode("ascii"))
    except (ValueError, OSError) as error:
        raise KeyOutputError(str(error)) from error
    finally:
        del private_key
    one_time = secrets.token_urlsafe(48)
    try:
        print("→ Backing up and migrating (this can take a minute)...")
        report = migrate.migrate_home(home, one_time)
        if not report.ok:
            print("✗ Migration finished WITH FAILURES (backup and external credential kept):")
            for failure in report.failures:
                print(f"    {failure}")
            if report.backup_path:
                print(f"  Backup: {report.backup_path}")
            return 1
        vault.add_key_slot(home, public_key_raw=public_key, password=one_time)
    finally:
        del one_time
    leftovers = migrate.scan_for_plaintext(home)
    if leftovers:
        print(f"✗ Plaintext remains after migration: {leftovers}")
        print(f"  Backup: {report.backup_path}")
        return 1
    print(f"✓ Migration complete ({len(report.databases)} DBs, {len(report.envelopes)} files, {len(report.frame_streams)} streams)")
    print(f"  External private key (ONLY credential, 0600): {output}")
    print(f"  Unlock with: HERMES_VAULT_PRIVATE_KEY={output}")
    if report.backup_path:
        print(f"  Pre-migration backup: {report.backup_path}")
        print("  Verify everything works, then delete it (it is PLAINTEXT).")
    print("  Keep this external credential in your deliberately selected protected storage.")
    return 0
