"""Deliberate offline plaintext export of a standalone encrypted migration rollback."""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import tarfile


def _output_path(value):
    from hermes_security.io import _home_for

    output = Path(value).expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError("Migration recovery output already exists; it was retained")
    output = output.resolve()
    if _home_for(output) is not None:
        raise ValueError("Plaintext recovery output must be outside every encrypted Hermes home")
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def _publish(backup, output, custody):
    from hermes_security.migration_archive import verified_tar

    parent = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    stage = ".migration-recovery-" + secrets.token_hex(16)
    count = 0
    try:
        # verified_tar authenticates COMPLETE rollback before the first plaintext stage byte.
        with verified_tar(backup, custody=custody) as source:
            fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            with os.fdopen(fd, "wb") as handle:
                with tarfile.open(fileobj=handle, mode="w|") as destination:
                    for member in source:
                        destination.addfile(member, source.extractfile(member) if member.isfile() else None)
                        count += 1
                handle.flush()
                os.fsync(handle.fileno())
        os.link(stage, output.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        try:
            os.fsync(parent)
        except OSError as error:
            raise OSError("Plaintext recovery was published but durability is uncertain; retain the encrypted backup") from error
    finally:
        try:
            os.unlink(stage, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)
    return count


def export_command(args) -> int:
    from hermes_cli.vault_cmd import _load_key_file, _password_from_env_or_prompt
    from hermes_platform.memory_protection import per_user_runtime_dir
    from hermes_security import vault
    from hermes_security.profile_binding import capture
    from hermes_security.errors import VaultError
    from hermes_security.migration_archive import read_backup_metadata
    from hermes_security.protected_workspace import protected_workspace

    if not args.output:
        print("Migration recovery requires an explicitly selected --output plaintext .tar outside every Hermes home")
        return 2
    try:
        backup, output = Path(args.migration_backup).expanduser().resolve(), _output_path(args.output)
        # Recovery does not consult/decrypt the lost live home. XDG_RUNTIME_DIR is
        # merely a candidate; protected_workspace still proves its RAM-only backing.
        with protected_workspace("migration-recovery", base=per_user_runtime_dir()) as temporary:
            metadata = temporary / ".hermes-vault"
            vault._atomic_write(metadata, read_backup_metadata(backup))
            key = os.environ.get("HERMES_VAULT_PRIVATE_KEY")
            unlocked = (vault.unlock_with_private_key(temporary, _load_key_file(key)) if key
                        else vault.unlock(temporary, _password_from_env_or_prompt()))
            try:
                count = _publish(backup, output, capture(temporary))
            finally:
                unlocked.wipe()
                vault.lock_now(temporary)
    except (OSError, ValueError, VaultError) as error:
        print(f"✗ Migration recovery refused; encrypted rollback retained: {error}")
        return 1
    print(f"Exported {count} original migration members to {output}")
    print("This explicitly selected recovery archive is PLAINTEXT. Keep it private; the encrypted rollback is unchanged.")
    return 0
