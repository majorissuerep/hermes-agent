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
    # Fork: the passphrase is NEVER read from the environment (env vars leak via
    # /proc/<pid>/environ, child inheritance and unit EnvironmentFiles — that
    # contradicts the vault's threat model). Interactive creation prompts on the
    # TTY; non-interactive callers create + add a key slot and use the key file.
    if not _tty_available():
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
    if not _tty_available():
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
        if getattr(args, "yes", False) is not True and not getattr(args, "key_only", False):
            answer = input("\nProceed with migration? A full tar backup is written first. [y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                print("Aborted; nothing was changed.")
                return 1
        if getattr(args, "key_only", False):
            # Fork policy: no passphrase may be created from/for a non-interactive
            # context. The vault is sealed to a fresh X25519 public key; the private
            # half goes to a 0600 file. That key file is the ONLY credential.
            import secrets as _secrets

            private_key, public_key = vault_mod.generate_keypair()
            key_out = Path(getattr(args, "key_out", None) or (home / "vault.key")).expanduser()
            if key_out.parent != Path(home):
                key_out.parent.mkdir(parents=True, exist_ok=True)
            key_out.write_bytes(vault_mod._b64e(private_key).encode("ascii"))
            os.chmod(key_out, 0o600)
            # Random one-time internal passphrase, discarded immediately: the scrypt
            # slot keeps the vault readable by future add-key runs (a passphrase slot
            # is metadata-only here); the durable credential is the key file.
            one_time = _secrets.token_urlsafe(48)
            print("→ Backing up and migrating (this can take a minute)...")
            report = mig.migrate_home(home, one_time)
            if not report.ok:
                print("✗ Migration finished WITH FAILURES (backup kept):")
                for failure in report.failures:
                    print(f"    {failure}")
                if report.backup_path:
                    print(f"  Backup: {report.backup_path}")
                return 1
            try:
                vault_mod.add_key_slot(home, public_key_raw=public_key, password=one_time)
            finally:
                del one_time
            leftovers = mig.scan_for_plaintext(home)
            if leftovers:
                print(f"✗ Plaintext remains after migration: {leftovers}")
                print(f"  Backup: {report.backup_path}")
                return 1
            print(f"✓ Migration complete ({len(report.databases)} DBs, {len(report.envelopes)} files, {len(report.frame_streams)} streams)")
            print(f"  Private key (ONLY credential, 0600): {key_out}")
            print(f"  Unlock with: HERMES_VAULT_PRIVATE_KEY={key_out}")
            if report.backup_path:
                print(f"  Pre-migration backup: {report.backup_path}")
                print("  Verify everything works, then delete it (it is PLAINTEXT).")
            print("  LOSE THE KEY FILE = LOSE THE DATA. There is no recovery.")
            return 0
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
            print(f"  (use 'hermes secure-vault unlock', or HERMES_VAULT_PRIVATE_KEY=<key-file path>, to unlock)")
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

    if command == "repair":
        if not vault_mod.vault_exists(home):
            print(f"✗ No vault at {home}; run 'hermes secure-vault migrate' first")
            return 1
        password = _password_from_env_or_prompt()
        try:
            vault_mod.unlock(home, password)
        except vault_errors.WrongMasterPasswordError:
            print("✗ Wrong master password")
            return 1
        from hermes_security.migrate import repair_clobbered_state

        report = repair_clobbered_state(home)
        sections = (
            ("sealed", "✓ Re-sealed {n} plaintext file(s) into envelopes:"),
            ("unwrapped", "✓ Unwrapped {n} database/stream file(s) a previous repair wrapped in envelopes:"),
            ("reframed", "✓ Re-framed {n} log/transcript stream(s) holding plaintext:"),
            ("restored", "✓ Restored {n} mangled file(s) from the pre-migration backup:"),
            ("skipped_dbs", "⚠ {n} PLAINTEXT database(s) left untouched (vault bypass suspected — investigate):"),
            ("unrecoverable", "✗ {n} file(s) could not be repaired:"),
        )
        for key, title in sections:
            items = report[key]
            if not items:
                continue
            print(title.format(n=len(items)))
            for rel in items[:20]:
                print(f"    {rel}")
            if len(items) > 20:
                print(f"    … and {len(items) - 20} more")
        if report["code_files"]:
            print(f"✓ Restored {report['code_files']} file(s) in hermes-agent.* code trees to plaintext (public code)")
        if not any(report[key] for key, _ in sections) and not report["code_files"]:
            print("○ Nothing to repair (every state file is in its canonical sealed form)")
        if report["unrecoverable"]:
            return 1
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
    from hermes_security.handoff import adopt_inherited_key

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
        except Exception as exc:  # noqa: BLE001 - a bad .env must not block 'secure-vault repair' advice
            print(
                f"⚠ hermes: sealed .env not loaded ({type(exc).__name__}); "
                "run 'hermes secure-vault repair'",
                file=sys.stderr,
            )
    except vault_errors.WrongMasterPasswordError:
        print("✗ Wrong master password")
        raise SystemExit(2)
