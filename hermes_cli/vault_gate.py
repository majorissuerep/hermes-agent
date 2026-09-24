"""Mandatory master-password gate — deliberately crypto-free.

This fork's key feature is master-password protection of ALL state. Policy:

- EVERY state-touching command requires a vault (initialized) AND an unlocked
  vault (master password supplied). That includes chat, gateway, serve,
  config, cron, update, doctor — everything.
- Only pure management/read-only surfaces bypass: ``secure-vault``/``vault``
  themselves, ``--version``, ``--help``, and ``update --check`` (diagnostics
  that persist nothing sensitive).
- No vault at all: REFUSE with instructions; on an interactive TTY offer to
  run the migration flow right there. Non-interactive callers (systemd,
  cron, serve) must set HERMES_MASTER_PASSWORD and create the vault first.
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

# `hermes deck` verbs that touch NO local state: they are RPCs to the already-unlocked session host over its
# 0600-token loopback socket (the same authority any same-user process has). Gating them would make an
# agent's `hermes deck send` (no TTY, no password in its terminal) fail in every vaulted home. Spawning a
# missing host still unlocks through `session_host._ensure_root_vault_unlocked`. `attach` and bare `deck`
# launch a TUI that reads config, so they stay gated.
_HOST_ONLY_DECK_VERBS = frozenset({"ls", "list", "send", "peek", "close", "interrupt"})


def vault_exists_light(home) -> bool:
    """Cheap vault probe without importing the crypto stack."""
    return (Path(home) / _META_FILENAME).is_file()


def _is_read_only(args: Any, command: Any) -> bool:
    if command in _READ_ONLY_COMMANDS or getattr(args, "version", False):
        return True
    # `hermes update --check`: pure diagnostic (git status + cache stamp).
    if command == "update" and getattr(args, "check", False):
        return True
    if command == "deck" and getattr(args, "deck_command", None) in _HOST_ONLY_DECK_VERBS:
        return True
    return False


def _tty_available() -> bool:
    """True when a real terminal is reachable through /dev/tty.

    NOT stdin.isatty(): under `curl ... | bash` stdin is the pipe, but the
    user's terminal is still reachable and getpass reads /dev/tty directly.
    """

    try:
        return os.isatty(os.open("/dev/tty", os.O_RDWR))
    except OSError:
        return False


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
            "  ('hermes secure-vault migrate'), then set HERMES_MASTER_PASSWORD\n"
            "  in this service's environment (systemd unit / EnvironmentFile).",
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
    if not vault_exists_light(home):
        if os.environ.get("HERMES_ALLOW_NO_VAULT") == "1":
            return  # documented CI/embedded harness escape (tests/conftest.py)
        _refuse_no_vault(home)
        return
    from hermes_cli.vault_cmd import gate_locked_vault

    gate_locked_vault(args, home)
