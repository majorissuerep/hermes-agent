"""``hermes vault`` subcommand: init / unlock / status / lock.

The vault is the fork's mandatory at-rest encryption layer.  ``init`` creates
vault metadata for the active Hermes home (refusing to overlay existing user
state); every later start of a state-touching command prompts for the master
password once per process (``HERMES_MASTER_PASSWORD`` covers daemons and
cron).
"""

from __future__ import annotations

import getpass
import os
import sys
from typing import Any, List, Optional

from hermes_security import errors as vault_errors
from hermes_security import vault as vault_mod


def _home(args: Any):
    from hermes_constants import get_hermes_home

    return get_hermes_home()


def _prompt_new_password() -> str:
    while True:
        first = getpass.getpass("Create master password: ")
        if len(first) < 8:
            print("✗ Master password must be at least 8 characters.")
            continue
        second = getpass.getpass("Repeat master password: ")
        if first != second:
            print("✗ Passwords do not match; try again.")
            continue
        return first


def _password_from_env_or_prompt(*, confirm: str = "Unlock master password: ") -> str:
    env_pw = os.environ.get("HERMES_MASTER_PASSWORD")
    if env_pw:
        return env_pw
    if not sys.stdin.isatty():
        print(
            "✗ The vault is locked and no terminal is available. "
            "Set HERMES_MASTER_PASSWORD for non-interactive use."
        )
        raise SystemExit(2)
    return getpass.getpass(confirm)


def cmd_vault(args: Any) -> int:
    command = getattr(args, "vault_command", None) or "status"
    home = _home(args)

    if command == "migrate":
        if vault_mod.vault_exists(home):
            print(f"✗ A vault already exists at {home}")
            return 1
        from hermes_security import migrate as mig

        # dry run first
        report = mig.migrate_home(home, "", dry_run=True)
        total = len(report.databases) + len(report.envelopes) + len(report.frame_streams)
        print(f"Migration plan for {home}:")
        print(f"  databases -> SQLCipher : {len(report.databases)}")
        for name in report.databases[:10]:
            print(f"    {name}")
        print(f"  files     -> envelopes : {len(report.envelopes)}")
        print(f"  streams   -> frames    : {len(report.frame_streams)}")
        print(f"  total: {total} item(s)")
        if getattr(args, "yes", False) is not True:
            answer = input("\nProceed with migration? A full tar backup is written first. [y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                print("Aborted; nothing was changed.")
                return 1
        password = _prompt_new_password()
        print("→ Backing up and migrating (this can take a minute)...")
        report = mig.migrate_home(home, password)
        if not report.ok:
            print(f"✗ Migration finished WITH FAILURES (backup kept):")
            for failure in report.failures:
                print(f"    {failure}")
            if report.backup_path:
                print(f"  Backup: {report.backup_path}")
            return 1
        leftovers = mig.scan_for_plaintext(home)
        if leftovers:
            print(f"✗ Plaintext remains after migration: {leftovers}")
            print(f"  Backup: {report.backup_path}")
            return 1
        print(f"✓ Migration complete ({len(report.databases)} DBs, {len(report.envelopes)} files, {len(report.frame_streams)} streams)")
        if report.backup_path:
            print(f"  Pre-migration backup: {report.backup_path}")
            print("  Verify everything works, then delete it (it is PLAINTEXT).")
        print("  LOSE THE PASSWORD = LOSE THE DATA. There is no recovery.")
        return 0

    if command == "init":
        if vault_mod.vault_exists(home):
            print(f"✗ A vault already exists at {home}")
            return 1
        password = _prompt_new_password()
        try:
            vault_mod.init_vault(home, password)
        except vault_errors.PlaintextStateError as exc:
            print(f"✗ {exc}")
            print(
                "  This home already contains unencrypted state. The vault never "
                "auto-migrates: move your secrets into the new vaulted home "
                "deliberately, then remove the plaintext copies."
            )
            return 1
        vault_mod.unlock(home, password)
        print(f"✓ Vault initialized at {home}")
        print("  All state written by this fork is now encrypted at rest.")
        print("  LOSE THE PASSWORD = LOSE THE DATA. There is no recovery.")
        return 0

    if command == "status":
        if not vault_mod.vault_exists(home):
            print(f"○ No vault at {home} (state writes will be refused; run 'hermes vault init')")
            return 1
        print(f"● Vault present at {home}")
        print(f"  Unlocked in this process: {'yes' if vault_mod.is_unlocked(home) else 'no'}")
        return 0

    if command == "unlock":
        if not vault_mod.vault_exists(home):
            print(f"✗ No vault at {home}; run 'hermes vault init' first")
            return 1
        password = _password_from_env_or_prompt()
        try:
            vault_mod.unlock(home, password)
        except vault_errors.WrongMasterPasswordError:
            print("✗ Wrong master password")
            return 1
        print(f"✓ Vault unlocked for this process ({home})")
        return 0

    if command == "lock":
        vault_mod.lock_now(home)
        print("✓ Vault locked in this process (keys dropped)")
        return 0

    print(f"✗ Unknown vault command: {command}")
    return 1


def gate_startup(args: Any) -> None:
    """Fail-closed startup gate: state-touching commands need an unlocked vault.

    Read-only surfaces (``--version``, ``vault`` itself, ``--help``) bypass.
    A vaulted-but-locked home prompts once for the master password (TTY) or
    reads ``HERMES_MASTER_PASSWORD`` (daemons/cron). A vaulted home whose
    password is wrong/unavailable exits before any state is touched.
    """

    from hermes_constants import get_hermes_home

    command = getattr(args, "command", None)
    if command in (None, "secure-vault", "vault") or getattr(args, "version", False):
        return
    home = get_hermes_home()
    if not vault_mod.vault_exists(home):
        # No vault: fail closed on the first thing that would write state.
        # (Reading is allowed for doctor/help-style flows; writers refuse.)
        return
    if vault_mod.is_unlocked(home):
        return
    try:
        password = _password_from_env_or_prompt()
        vault_mod.unlock(home, password)
        # An import-time config read (parser build, plugin discovery) ran while
        # the vault was locked and cached the degraded empty parse; drop every
        # config cache so post-unlock loads see the real decrypted file.
        from hermes_cli import config as _cfg

        _cfg._RAW_CONFIG_CACHE.clear()
        _cfg._LAST_EXPANDED_CONFIG_BY_PATH.clear()
        try:
            _cfg._LOAD_CONFIG_CACHE.clear()
        except AttributeError:
            for key in [k for k in vars(_cfg) if k.startswith("_LOAD_CONFIG_CACHE")]:
                obj = getattr(_cfg, key)
                if isinstance(obj, dict):
                    obj.clear()
    except vault_errors.WrongMasterPasswordError:
        print("✗ Wrong master password")
        raise SystemExit(2)
