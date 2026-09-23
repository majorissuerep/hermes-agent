"""Fail-closed vault startup gate — deliberately crypto-free.

Importing this module must NEVER pull ``cryptography`` (its native lib locks
on Windows self-update; upstream keeps it out of update dispatch).  The heavy
``hermes_cli.vault_cmd`` (and with it the crypto stack) loads ONLY when a
vault actually exists at the active home.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_META_FILENAME = ".hermes-vault"


def vault_exists_light(home) -> bool:
    """Cheap vault probe without importing the crypto stack."""
    return (Path(home) / _META_FILENAME).is_file()


def gate_startup(args: Any) -> None:
    """State-touching commands need an unlocked vault.

    - Read-only surfaces (``--version``, ``secure-vault``/``vault`` themselves)
      bypass.
    - No vault: bootstrap mode — plaintext writes stay possible (installs,
      tests), with a one-line warning on interactive TTYs.
    - Vault present but locked: prompt once (TTY) or read
      ``HERMES_MASTER_PASSWORD`` (daemons); wrong password exits before any
      state is touched.
    """

    command = getattr(args, "command", None)
    if command in (None, "secure-vault", "vault") or getattr(args, "version", False):
        return
    from hermes_constants import get_hermes_home

    home = get_hermes_home()
    if not vault_exists_light(home):
        if sys.stderr.isatty():
            print(
                f"⚠ No secure vault at {home} — new state is written UNENCRYPTED.\n"
                "  Run 'hermes secure-vault migrate' to switch to encrypted storage.",
                file=sys.stderr,
            )
        return
    from hermes_cli.vault_cmd import gate_locked_vault

    gate_locked_vault(args, home)
