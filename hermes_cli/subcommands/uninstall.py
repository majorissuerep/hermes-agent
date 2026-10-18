"""``hermes uninstall`` subcommand parser."""

from __future__ import annotations

import argparse
from typing import Callable

from hermes_cli.subcommands._shared import add_yes_flag


def build_uninstall_parser(subparsers, *, cmd_uninstall: Callable) -> None:
    """Attach the ``uninstall`` subcommand to ``subparsers``."""
    uninstall_parser = subparsers.add_parser(
        "uninstall", help="Uninstall Hermes Agent",
        description="Remove Hermes Agent from your system. Can keep configs/data for reinstall.")
    modes = uninstall_parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--full", action="store_true",
        help="Full uninstall - remove everything including configs and data")
    modes.add_argument(
        "--data",
        action="store_true",
        help="Remove only the active Hermes home's user data, leaving installed code "
        "intact — the one mode that works on Nix / bundled-app / Docker installs",
    )
    add_yes_flag(uninstall_parser, "Skip confirmation prompts")
    uninstall_parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what uninstall would remove without changing anything")
    uninstall_parser.set_defaults(func=cmd_uninstall)
