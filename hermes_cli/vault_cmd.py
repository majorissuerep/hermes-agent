"""``hermes vault`` subcommand: init / unlock / status / lock.

The vault is the fork's mandatory at-rest encryption layer.  ``init`` creates
vault metadata for the active Hermes home (refusing to overlay existing user
state); every later start of a state-touching command prompts for the master
password once per process on the TTY, or unlocks from a key file
(``HERMES_VAULT_PRIVATE_KEY`` — a PATH, never a passphrase, in the env; the
passphrase itself is never read from the environment).
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path
from typing import Any, List, Optional

from hermes_cli import vault_gate
from hermes_security import errors as vault_errors
from hermes_security import vault as vault_mod


def _home(args: Any):
    from hermes_constants import get_hermes_home

    return get_hermes_home()


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
    # Fork: the passphrase is NEVER read from the environment (env vars leak via
    # /proc/<pid>/environ, child inheritance and unit EnvironmentFiles — that
    # contradicts the vault's threat model). Interactive creation prompts on the
    # TTY; non-interactive callers create + add a key slot and use the key file.
    if not vault_gate._tty_available():
        print(
            "✗ No terminal available to create a master password.\n"
            "  Interactive: run 'hermes secure-vault migrate' from a terminal.\n"
            "  Non-interactive: not supported — create the vault once interactively,\n"
            "  then 'hermes secure-vault keygen' + 'add-key' for daemons (key file, never env).",
            file=sys.stderr,
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
    # Fork: NO env passphrase, ever. Non-interactive unlock is the key-file path
    # (HERMES_VAULT_PRIVATE_KEY), handled in get_vault; this is the human path.
    if not vault_gate._tty_available():
        print(
            "✗ The vault is locked and no terminal is available.\n"
            "  Unlock with a key file (HERMES_VAULT_PRIVATE_KEY=/path/to/key, 0600, never a\n"
            "  passphrase in the environment), or run 'hermes secure-vault unlock' in a terminal.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return getpass.getpass(confirm)


def cmd_vault(args: Any) -> int:
    command = getattr(args, "vault_command", None) or "status"
    home = _home(args)

    if command == "migrate":
        from hermes_cli.vault_migration import migrate_command

        return migrate_command(args, home)

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
        from hermes_cli.vault_status import print_status

        return print_status(home)

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
        gate_locked_vault(args, home)
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

    if command == "repair":
        from hermes_cli.vault_repair import run

        return run(args, home)

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
    from hermes_security.handoff import adopt_inherited_key
    from hermes_security.session_authority import SessionAuthorityError

    try:
        # A detached child (the session host) inherits its launcher's unlocked key over a pipe fd.
        if not (adopt_inherited_key() and vault_mod.is_unlocked(home)):
            # Daemon path FIRST: a key file (PATH in env, never secret material). Then
            # the human path: TTY passphrase. The passphrase is never read from env.
            key_path = os.environ.get("HERMES_VAULT_PRIVATE_KEY")
            if key_path:
                vault_mod.unlock_with_private_key(home, _load_key_file(key_path))
            else:
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
        # The import-time dotenv load ran locked and deferred the sealed .env;
        # without this reload no .env key ever reaches the process.
        from hermes_cli.env_loader import load_hermes_dotenv

        try:
            load_hermes_dotenv()
        except Exception as exc:
            print(
                f"⚠ hermes: sealed .env not loaded ({type(exc).__name__}); "
                "run 'hermes secure-vault repair'",
                file=sys.stderr,
            )
    except SessionAuthorityError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except vault_errors.WrongMasterPasswordError:
        print("✗ Wrong master password")
        raise SystemExit(2)
