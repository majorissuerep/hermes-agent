"""``hermes deck`` subcommand parser (implementation: ``hermes_cli/deck_cmd.py``)."""

from __future__ import annotations

import argparse


def _ref(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("ref", help="Session: #N in this profile, or <profile>#N")


def build_deck_parser(subparsers) -> None:
    from hermes_cli.deck_cmd import cmd_deck

    deck = subparsers.add_parser(
        "deck", help="Every open session on this machine: list, message, close, attach",
        description="The session deck. Sessions stay open — across terminal exits, crashes and "
            "reboots — until closed with /close or `hermes deck close`. Bare `hermes deck` opens the "
            "interactive deck.")
    deck.set_defaults(func=cmd_deck, deck_command=None)
    sub = deck.add_subparsers(dest="deck_command")

    ls = sub.add_parser("ls", aliases=["list"], help="List open sessions")
    ls.add_argument("--profile", default=None, help="Only this profile (default: all)")
    ls.add_argument("--closed", action="store_true", help="Include closed sessions")
    ls.add_argument("--json", action="store_true", help="Machine-readable output")

    send = sub.add_parser("send", help="Queue a message as a session's next turn")
    _ref(send)
    send.add_argument("message", nargs="*", help="Message text (or --file, or stdin)")
    send.add_argument("--file", default=None, help="Read the message from a file")
    send.add_argument("--wait", type=float, default=0, metavar="SECONDS",
                      help="Wait up to SECONDS for the session's reply and print it")

    for name, text in (("close", "Close a session (ends it everywhere, removes it from the deck)"),
                       ("interrupt", "Stop a session's running turn"),
                       ("attach", "Open a session in this terminal")):
        _ref(sub.add_parser(name, help=text))

    peek = sub.add_parser("peek", help="Show a session's latest messages")
    _ref(peek)
    peek.add_argument("-n", type=int, default=6, help="How many messages (default 6)")
