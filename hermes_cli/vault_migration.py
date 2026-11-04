"""Explicit continuation of a credential-bound, interrupted vault migration."""
from __future__ import annotations

from hermes_security.errors import VaultError


def resume_command(args, home) -> int:
    from hermes_cli.vault_cmd import gate_locked_vault
    from hermes_security import migrate

    print("→ Resuming the authenticated migration checkpoint; the encrypted rollback is retained.")
    gate_locked_vault(args, home)
    try:
        report = migrate.migrate_home(home, None)
    except (OSError, ValueError, VaultError) as error:
        print(f"✗ Migration continuation refused: {error}")
        print("  Keep the original home, encrypted rollback and original credential for recovery.")
        return 1
    if report.backup_path:
        print(f"  Encrypted rollback: {report.backup_path}")
    if report.failures:
        print("✗ Migration remains incomplete; original and converted files were retained:")
        for failure in report.failures:
            print(f"    {failure}")
        return 1
    print("✓ Migration complete; encrypted state and rollback verified.")
    return 0


def _confirm(args, home, report):
    print(f"Migration plan for {home}:")
    print(f"  databases -> SQLCipher : {len(report.databases)}")
    for name in report.databases[:10]:
        print(f"    {name}")
    print(f"  files -> envelopes : {len(report.envelopes)}")
    print(f"  streams -> frames : {len(report.frame_streams)}")
    if report.browser_profiles:
        print(f"  browser profiles -> verified encrypted archives : {len(report.browser_profiles)}")
    if getattr(args, "yes", False) or getattr(args, "key_only", False):
        return True
    answer = input("Proceed with migration? An encrypted rollback is verified before conversion. [y/N] ")
    if answer.strip().lower() in {"y", "yes"}:
        return True
    print("Aborted; nothing was changed.")
    return False


def _report(home, report):
    from hermes_security import migrate

    if report.backup_path:
        print(f"  Encrypted rollback: {report.backup_path}")
    if report.failures:
        print("✗ Migration remains incomplete; the original credential and encrypted rollback are retained:")
        for failure in report.failures:
            print(f"    {failure}")
        print("  Run 'hermes secure-vault migrate --yes' with the original credential to resume.")
        return 1
    if migrate.scan_for_plaintext(home):
        print("✗ Unconverted state remains; preserve the rollback and resume migration.")
        return 1
    print(f"✓ Migration complete ({len(report.databases)} DBs, {len(report.envelopes)} files, {len(report.frame_streams)} streams)")
    print("  Keep the original credential with the encrypted rollback for disaster recovery.")
    return 0


def migrate_command(args, home):
    from hermes_cli.vault_cmd import _prompt_new_password
    from hermes_cli.vault_key_creation import KeyOutputError, key_destination, migrate_with_key_file
    from hermes_security import migrate, migration_state, vault

    if vault.vault_exists(home):
        if migration_state.pending(home):
            return resume_command(args, home)
        print(f"✗ A vault already exists at {home}; no authenticated pending migration exists")
        return 1
    try:
        key_only = getattr(args, "key_only", False)
        try:
            key_out = key_destination(home, getattr(args, "key_out", None)) if key_only else None
        except (OSError, ValueError) as error:
            raise KeyOutputError(str(error)) from error
        if not _confirm(args, home, migrate.migrate_home(home, "", dry_run=True)):
            return 1
        if key_only:
            return migrate_with_key_file(home, key_out)
        password = _prompt_new_password()
        print("→ Verifying encrypted rollback and migrating...")
        report = migrate.migrate_home(home, password)
    except KeyOutputError as error:
        print(f"✗ Key-only creation refused: {error}")
        return 2
    except (OSError, ValueError, VaultError) as error:
        print(f"✗ Migration refused; original state and any encrypted rollback retained: {error}")
        return 1
    return _report(home, report)
