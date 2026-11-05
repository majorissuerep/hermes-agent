"""Reject unsafe key-only migration arguments before startup can persist state."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


def admit_vault_command(argv: list[str]) -> None:
    """Reuse canonical argv, profile and vault parsers; perform only read-only admission."""
    from hermes_cli._startup_fast import normalize_hermes_home_env, try_session_proxy

    normalize_hermes_home_env()
    try_session_proxy(argv)  # The owning authority must admit even help before argparse can exit.

    from hermes_cli._parser import command_argv
    from hermes_cli._profile_selection import profile_home, select_profile, sudo_user_profile_env
    from hermes_cli.subcommands.secure_vault import build_secure_vault_parser
    from hermes_cli.vault_key_policy import key_destination
    from hermes_constants import get_process_hermes_home

    try:
        name, consume, index, _sticky = select_profile(argv)
        selected = get_process_hermes_home()
        if name is not None:
            selected = profile_home(name)
            if selected is None:
                alternative = sudo_user_profile_env(name)
                if alternative is None:
                    raise ValueError(f"Profile '{name}' does not exist; run `hermes profile list`")
                selected = Path(alternative)
        tail = list(argv)
        if consume:
            del tail[index:index + consume]
        parser = argparse.ArgumentParser(prog="hermes")
        build_secure_vault_parser(parser.add_subparsers(dest="command", required=True))
        args = parser.parse_args(command_argv(tail))
        if args.vault_command == "migrate" and args.key_only:
            key_destination(selected, args.key_out)
    except (ValueError, OSError) as exc:
        print(f"✗ Key-only creation refused: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
