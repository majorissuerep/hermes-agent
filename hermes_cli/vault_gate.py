"""Mandatory master-password gate — deliberately crypto-free.

This fork's key feature is master-password protection of ALL state. Policy:

- EVERY state-touching command requires a vault (initialized) AND an unlocked
  vault (master password supplied). That includes chat, config, cron, update, doctor — everything.
- Only pure management/read-only surfaces bypass: ``secure-vault``/``vault``
  themselves, ``--version``, ``--help``, and ``update --check`` (diagnostics
  that persist nothing sensitive).
- ``graphify`` builds project-local artifacts and reads no Hermes credentials
  or encrypted state, so it is also available to an agent's terminal child.
- No vault at all: REFUSE with instructions; on an interactive TTY offer to
  run the migration flow right there. Non-interactive callers (systemd,
  headless tasks) unlock with a key file (HERMES_VAULT_PRIVATE_KEY — a PATH,
  never a passphrase, in the environment).
- ``HERMES_ALLOW_NO_VAULT=1`` is the documented escape for CI/embedded test
  harnesses only — set by tests/conftest.py, never in production installs.

Importing this module must NEVER pull ``cryptography`` (its native lib locks
on Windows self-update; upstream keeps it out of update dispatch). The heavy
``hermes_cli.vault_cmd`` (and the crypto stack) loads ONLY when a vault
actually exists at the active home or the migrate offer is accepted.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_META_FILENAME = ".hermes-vault"

# Commands that manage the vault or are pure read-only diagnostics.
_READ_ONLY_COMMANDS = frozenset({"secure-vault", "vault"})



def vault_exists_light(home) -> bool:
    """Cheap vault probe without importing the crypto stack."""
    return _owning_vault_home(home) is not None


def _owning_vault_home(home) -> Path | None:
    path = Path(home).expanduser().resolve()
    return next((parent for parent in (path, *path.parents)
                 if (parent / _META_FILENAME).is_file()), None)


def _is_read_only(args: Any, command: Any) -> bool:
    if command in _READ_ONLY_COMMANDS or getattr(args, "version", False):
        return True
    # `hermes update --check`: pure diagnostic (git status + cache stamp).
    if command == "update" and getattr(args, "check", False):
        return True
    if command == "graphify" and not getattr(args, "oneshot", None):
        return True
    return False


def _tty_available() -> bool:
    """A terminal inherited on stdin or reachable through /dev/tty.

    Updaters start a new process session but retain terminal stdin. Conversely,
    `curl ... | bash` has pipe stdin but getpass can still open /dev/tty.
    """

    if sys.stdin is not None and sys.stdin.isatty():
        return True
    try:
        fd = os.open("/dev/tty", os.O_RDWR)
    except OSError:
        return False
    try:
        return os.isatty(fd)
    finally:
        os.close(fd)


def _refuse_no_vault(home) -> None:
    tty = _tty_available()
    print(
        f"✗ Master password required — no secure vault exists at {home}.\n"
        "  This Hermes fork encrypts ALL state (sessions, configs, keys, logs)\n"
        "  with your master password. It refuses to run unencrypted.",
        file=sys.stderr,
    )
    if tty:
        answer = input("Create the vault now? (backs up and encrypts this home) [Y/n] ").strip().lower()
        if answer in {"", "y", "yes"}:
            from types import SimpleNamespace

            from hermes_cli.vault_cmd import cmd_vault

            rc = cmd_vault(SimpleNamespace(vault_command="migrate", yes=True))
            if rc == 0:
                return  # vault created + unlocked inline; caller proceeds
            raise SystemExit(rc or 2)
        print(
            "  Run 'hermes secure-vault migrate' to set it up, then retry.",
            file=sys.stderr,
        )
    else:
        print(
            "  Non-interactive process: create the vault once from a terminal\n"
            "  ('hermes secure-vault migrate'), add a key slot ('hermes secure-vault\n"
            "  keygen' + 'add-key --public-key <file>'), and unlock with the key file:\n"
            "  HERMES_VAULT_PRIVATE_KEY=/path/to/key (0600). The passphrase is never\n"
            "  read from the environment.",
            file=sys.stderr,
        )
    raise SystemExit(2)


def gate_startup(args: Any) -> None:
    """Master-password gate for every state-touching command."""

    command = getattr(args, "command", None)
    if _is_read_only(args, command):
        return
    from hermes_constants import get_hermes_home

    home = get_hermes_home()
    owner = _owning_vault_home(home)
    if owner is None:
        if os.environ.get("HERMES_ALLOW_NO_VAULT") == "1":
            return  # documented CI/embedded harness escape (tests/conftest.py)
        _refuse_no_vault(home)
        return
    from hermes_cli.vault_cmd import gate_locked_vault

    # Profiles beneath a machine vault are already sealed by that vault. Their
    # grant stays profile-scoped, but its derived key belongs to the ancestor.
    gate_locked_vault(args, owner)
