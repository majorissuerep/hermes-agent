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
from pathlib import Path
from typing import Any, List, Optional

from hermes_security import errors as vault_errors
from hermes_security import vault as vault_mod


def _home(args: Any):
    from hermes_constants import get_hermes_home

    return get_hermes_home()


def _tty_available() -> bool:
    """True when a real terminal can be reached for a password prompt.

    NOT sys.stdin.isatty(): under `curl ... | bash` stdin is the pipe, but
    /dev/tty is still the user's terminal and getpass reads /dev/tty
    directly. Refusing on !isatty() wrongly locked out exactly that flow.
    """

    try:
        return os.isatty(os.open("/dev/tty", os.O_RDWR))
    except OSError:
        return False


def _load_key_file(path: str) -> bytes:
    """Read a raw 32-byte X25519 key from *path* (base64 or hex text)."""

    import base64 as _b64

    raw = open(path, "r", encoding="utf-8").read().strip()
    candidates: list[bytes] = []
    try:
        candidates.append(_b64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except Exception:
        pass
    try:
        candidates.append(bytes.fromhex(raw))
    except ValueError:
        pass
    for cand in candidates:
        if len(cand) == 32:
            return cand
    raise ValueError(f"not a 32-byte key in base64 or hex ({path})")


def _prompt_new_password() -> str:
    # Non-interactive callers (scripts, takeover, CI): honor the env password
    # for CREATION too — it was already the unlock path for daemons.
    env_pw = os.environ.get("HERMES_MASTER_PASSWORD")
    if env_pw and not _tty_available():
        if len(env_pw) < 8:
            print("✗ HERMES_MASTER_PASSWORD must be at least 8 characters.")
            raise SystemExit(2)
        return env_pw
    if not _tty_available() and not env_pw:
        print(
            "✗ No terminal available to create a master password. "
            "Set HERMES_MASTER_PASSWORD for non-interactive use."
        )
        raise SystemExit(2)
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
    if not _tty_available():
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
        # The CLI seeds the home scaffold (dirs, SOUL.md, logs) before any
        # command runs, so a fresh home is never literally empty: route to
        # migrate, which handles scaffold + real state identically and safely.
        print("→ Home contains the first-run scaffold; using migrate (safe for fresh homes).")
        args.vault_command = "migrate"
        return cmd_vault(args)
        password = _prompt_new_password()
        try:
            vault_mod.init_vault(home, password)
        except vault_errors.PlaintextStateError as exc:
            print(f"✗ {exc}")
            print(
                "  This home already contains state. Use 'hermes secure-vault migrate'\n"
                "  instead: it backs everything up and converts it in place onto\n"
                "  the encrypted vault (sessions and configs preserved)."
            )
            return 1
        vault_mod.unlock(home, password)
        print(f"✓ Vault initialized at {home}")
        print("  All state written by this fork is now encrypted at rest.")
        print("  LOSE THE PASSWORD = LOSE THE DATA. There is no recovery.")
        return 0

    if command == "status":
        if not vault_mod.vault_exists(home):
            print(f"○ No vault at {home} (state writes will be refused; run 'hermes secure-vault migrate')")
            return 1
        status = vault_mod.vault_status(home)
        meta = status["meta"]
        print(f"● Vault present at {home}")
        print(f"  Integrity        : {status['integrity']}")
        print(f"  Unlocked in proc : {'yes' if status['unlocked'] else 'no'}")
        if meta:
            kdf = meta.get("kdf", "unknown")
            n = meta.get("kdf_n", "?")
            r = meta.get("kdf_r", "?")
            p = meta.get("kdf_p", "?")
            print(f"  KDF              : {kdf} (n={n}, r={r}, p={p})")
            print(f"  Version          : v{meta.get('version', '?')}")
            # salt is public metadata; show hex + length for verification
            salt_b64 = meta.get("salt", "")
            if salt_b64:
                import base64 as _b64

                salt_hex = _b64.urlsafe_b64decode(salt_b64).hex()
                print(f"  Salt             : {salt_hex} ({len(salt_b64)} b64 chars)")
        envelopes, dbs, frames = status["encrypted_files"]
        print(f"  Encrypted files  : {envelopes} envelope(s), {dbs} database(s), {frames} frame stream(s)")
        if not status["unlocked"]:
            print(f"  (use 'hermes secure-vault unlock' or set HERMES_MASTER_PASSWORD to unlock)")
        return 0

    if command == "unlock":
        if not vault_mod.vault_exists(home):
            print(f"✗ No vault at {home}; run 'hermes secure-vault migrate' first")
            return 1
        # Private-key unlock: --private-key / HERMES_VAULT_PRIVATE_KEY (path)
        key_path = getattr(args, "private_key", None) or os.environ.get("HERMES_VAULT_PRIVATE_KEY")
        if key_path:
            try:
                private_key = _load_key_file(key_path)
            except (OSError, ValueError) as exc:
                print(f"✗ Cannot read private key: {exc}")
                return 1
            try:
                vault_mod.unlock_with_private_key(home, private_key)
            except vault_errors.WrongMasterPasswordError:
                print("✗ This private key does not open this vault")
                return 1
            except vault_errors.VaultLockedError as exc:
                print(f"✗ {exc}")
                return 1
            print(f"✓ Vault unlocked via key slot for this process ({home})")
            return 0
        password = _password_from_env_or_prompt()
        try:
            vault_mod.unlock(home, password)
        except vault_errors.WrongMasterPasswordError:
            print("✗ Wrong master password")
            return 1
        print(f"✓ Vault unlocked for this process ({home})")
        return 0

    if command == "keygen":
        private_key, public_key = vault_mod.generate_keypair()
        print("X25519 keypair generated.")
        print()
        print(f"  PUBLIC  (base64): {vault_mod._b64e(public_key)}")
        print()
        print("  PRIVATE (base64): " + vault_mod._b64e(private_key))
        print()
        print("  ⚠ The private key is shown ONCE and never stored by Hermes.")
        print("    Store it wherever YOU want (password manager, printed, USB,")
        print("    another machine). The vault stores only the PUBLIC half.")
        print("    LOSE IT = LOSE THE VAULT. There is no recovery.")
        return 0

    if command == "add-key":
        if not vault_mod.vault_exists(home):
            print(f"✗ No vault at {home}; run 'hermes secure-vault migrate' first")
            return 1
        pub_path = getattr(args, "public_key", None)
        if not pub_path:
            print("✗ --public-key <path> required (base64 or hex, 32 bytes)")
            return 1
        try:
            public_key = _load_key_file(pub_path)
        except (OSError, ValueError) as exc:
            print(f"✗ Cannot read public key: {exc}")
            return 1
        password = _password_from_env_or_prompt()
        try:
            vault_mod.add_key_slot(home, public_key_raw=public_key, password=password)
        except vault_errors.WrongMasterPasswordError:
            print("✗ Wrong master password")
            return 1
        print("✓ Key slot added — vault is now also openable with the matching private key")
        print("  (the system stored ONLY the public half)")
        return 0

    if command == "remove-key":
        if not vault_mod.vault_exists(home):
            print(f"✗ No vault at {home}")
            return 1
        slot = getattr(args, "slot", None)
        if slot is None:
            print("✗ --slot <index> required (see 'hermes secure-vault slots')")
            return 1
        vault_mod.remove_key_slot(home, index=slot)
        print(f"✓ Key slot {slot} removed")
        return 0

    if command == "slots":
        if not vault_mod.vault_exists(home):
            print(f"○ No vault at {home}")
            return 1
        slots = vault_mod.vault_status(home)["meta"].get("key_slots") or []
        if not slots:
            print("○ No key slots (passphrase only)")
            return 0
        for i, slot in enumerate(slots):
            print(f"  [{i}] {slot.get('type', '?')}")
        return 0

    if command == "lock":
        vault_mod.lock_now(home)
        print("✓ Vault locked in this process (keys dropped)")
        return 0

    print(f"✗ Unknown vault command: {command}")
    return 1


def _vault_exists_light(home) -> bool:
    """Cheap vault probe WITHOUT importing the crypto stack (cryptography
    must stay out of update dispatch: its native lib locks on Windows
    self-update). Mirrors vault._META_FILENAME."""
    return (Path(home) / ".hermes-vault").is_file()


def gate_locked_vault(args: Any, home) -> None:
    """Locked-vault half of the startup gate (crypto stack already loaded)."""

    from hermes_security import vault as vault_mod

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
