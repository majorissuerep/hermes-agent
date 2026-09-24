"""``hermes deck`` — one entry point for every open session on this machine.

Every verb goes through the session host (started on demand), so a terminal, a script and an agent's
``sessions`` tool all see the same deck and the same rules. Bare ``hermes deck`` opens the TUI deck.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

# state -> (glyph, rich style). Order is also the sort priority in the table (needs-you first).
STATE_STYLE = {
    "waiting": ("◐", "bold yellow"),
    "running": ("●", "bold green"),
    "idle": ("○", "cyan"),
    "detached": ("◌", "blue"),
    "dormant": ("·", "dim"),
}


def _client():
    from hermes_cli.session_host import connect

    return connect()


def _profile_param() -> Dict[str, str]:
    """The caller's profile, plus — when an agent runs this from its terminal — the session speaking
    (``HERMES_SESSION_ID`` is bound per command), so the recipient sees who sent it and the host refuses
    a session closing or messaging itself."""
    from hermes_cli.session_deck import current_profile

    params = {"profile": current_profile()}
    if sender := os.environ.get("HERMES_SESSION_ID", "").strip():
        params["from_session_id"] = sender
    return params


def _ago(ts: float) -> str:
    delta = max(0, int(time.time() - ts))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if delta >= size:
            return f"{delta // size}{unit}"
    return f"{delta}s"


def short_path(path: str, width: int = 30) -> str:
    """``~``-relative, and the TAIL kept when too long — the project dir is the informative end."""
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + os.sep):
        path = "~" + path[len(home):]
    return path if len(path) <= width else "…" + path[-(width - 1):]


def _summary(sessions: List[Dict[str, Any]]) -> str:
    counts = {state: sum(1 for s in sessions if s["state"] == state) for state in STATE_STYLE}
    parts = [f"{len(sessions)} open"] + [f"{glyph} {counts[state]} {state}" for state, (glyph, _style)
                                        in STATE_STYLE.items() if counts[state]]
    return "  ·  ".join(parts)


def render_table(sessions: List[Dict[str, Any]], *, console=None) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    console = console or Console()
    if not sessions:
        console.print("[dim]No open sessions. Start one with[/] [bold]hermes --tui[/][dim]; it stays open until[/] "
                      "[bold]/close[/][dim].[/]")
        return
    table = Table(box=None, pad_edge=False, show_edge=False, header_style="dim", expand=False)
    for name, kw in (("", {"width": 1}), ("ref", {"style": "bold"}), ("title", {"max_width": 44, "no_wrap": True}),
                     ("state", {}), ("where", {"style": "dim", "max_width": 32, "no_wrap": True}),
                     ("msgs", {"justify": "right", "style": "dim"}), ("active", {"justify": "right", "style": "dim"})):
        table.add_column(name, **kw)
    for row in sessions:
        glyph, style = STATE_STYLE.get(row["state"], ("?", ""))
        owner = row.get("owner_surface") or ""
        where = short_path(row.get("cwd") or "")
        if owner and owner not in ("tui", "desktop"):
            where = f"{owner} · {where}" if where else owner
        table.add_row(Text(glyph, style=style), row["ref"], row["title"] or Text("untitled", style="dim italic"),
                      Text(row["state"], style=style), where, str(row["message_count"]), _ago(row["last_active"]))
    console.print(table)
    console.print(f"[dim]{_summary(sessions)}[/]")


def cmd_deck_ls(args: argparse.Namespace) -> int:
    params: Dict[str, Any] = {"include_closed": bool(args.closed)}
    if args.profile:
        params["profiles"] = [args.profile]
    with _client() as client:
        sessions = client.call("deck.list", params)["sessions"]
    order = list(STATE_STYLE)
    sessions.sort(key=lambda s: (order.index(s["state"]) if s["state"] in order else 99, -s["last_active"]))
    if args.json:
        print(json.dumps(sessions, indent=2))
    else:
        render_table(sessions)
    return 0


def _read_message(args: argparse.Namespace) -> str:
    if args.file:
        with open(args.file, encoding="utf-8") as handle:
            return handle.read()
    if args.message:
        return " ".join(args.message)
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def cmd_deck_send(args: argparse.Namespace) -> int:
    message = _read_message(args).strip()
    if not message:
        print("✗ nothing to send (pass a message, --file, or pipe stdin)", file=sys.stderr)
        return 2
    wait = float(args.wait or 0)
    with _client() as client:
        result = client.call("deck.send", {**_profile_param(), "target": args.ref, "message": message,
                                           "wait_s": wait}, timeout=wait + 60)
    woke = " (woke it)" if result["woke"] else ""
    if result["status"] == "settled" and result.get("reply") is not None:
        print(f"✓ {result['ref']} replied{woke}:\n")
        print(result["reply"])
    elif result["status"] in ("queued", "claimed"):
        print(f"✓ queued for {result['ref']}{woke} — it runs as that session's next turn")
    else:
        print(f"✗ {result['ref']}: {result['status']} {result.get('error') or ''}".rstrip(), file=sys.stderr)
        return 1
    return 0


def cmd_deck_close(args: argparse.Namespace) -> int:
    with _client() as client:
        result = client.call("deck.close", {**_profile_param(), "target": args.ref})
    print(f"✓ closed {result['ref']}" if result["closed"] else f"○ {result['ref']} was already closed")
    return 0


def cmd_deck_interrupt(args: argparse.Namespace) -> int:
    with _client() as client:
        result = client.call("deck.interrupt", {**_profile_param(), "target": args.ref})
    print(f"✓ interrupted {result['ref']}" if result["interrupted"] else f"○ {result['ref']} was not running here")
    return 0


def cmd_deck_peek(args: argparse.Namespace) -> int:
    from rich.console import Console
    from rich.markdown import Markdown

    with _client() as client:
        result = client.call("deck.peek", {**_profile_param(), "target": args.ref, "limit": args.n})
    console, row = Console(), result["session"]
    glyph, style = STATE_STYLE.get(row["state"], ("?", ""))
    console.print(f"[{style}]{glyph}[/] [bold]{row['ref']}[/] {row['title'] or 'untitled'}  "
                  f"[dim]{row['state']} · {row['model']} · {short_path(row['cwd'], 40)}[/]")
    for message in result["messages"]:
        who = "[bold cyan]you[/]" if message["role"] == "user" else "[bold magenta]hermes[/]"
        console.print(f"\n{who}")
        console.print(Markdown(message["text"]))
    return 0


def cmd_deck_attach(args: argparse.Namespace) -> int:
    """Open the session in this terminal (host-attached TUI); other terminals showing it stay attached."""
    from hermes_cli.tui_deck_launch import launch_deck_attach

    with _client() as client:
        row = client.call("deck.resolve", {**_profile_param(), "target": args.ref})["session"]
    return launch_deck_attach(row)


def cmd_deck(args: argparse.Namespace) -> int:
    handler = {
        None: _cmd_deck_open, "ls": cmd_deck_ls, "list": cmd_deck_ls, "send": cmd_deck_send,
        "close": cmd_deck_close, "peek": cmd_deck_peek, "interrupt": cmd_deck_interrupt,
        "attach": cmd_deck_attach,
    }[getattr(args, "deck_command", None)]
    from hermes_cli.session_host import HostRpcError, HostUnavailable

    try:
        return handler(args)
    except HostRpcError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    except HostUnavailable as exc:
        print(f"✗ session host unavailable: {exc}", file=sys.stderr)
        return 1


def _cmd_deck_open(args: argparse.Namespace) -> int:
    """Bare ``hermes deck``: the TUI deck on a terminal, the table otherwise."""
    if sys.stdout.isatty():
        from hermes_cli.tui_deck_launch import launch_deck_tui

        return launch_deck_tui()
    return cmd_deck_ls(argparse.Namespace(closed=False, profile=None, json=False))
