"""Conversation names shared by the REPL, TUI and native terminal title bars."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path


def generate_session_name(messages: list[dict], *, main_runtime: dict | None = None) -> str:
    from agent.message_content import flatten_message_text
    from agent.title_generator import generate_title

    turns = [f"{m['role']}: {flatten_message_text(m.get('content'))}" for m in messages
             if m.get("role") in {"user", "assistant"} and m.get("content")]
    if not turns:
        raise ValueError("Send a message first, or use /name <name>.")
    title = generate_title("\n".join(turns[-6:])[-1000:], main_runtime=main_runtime)
    if not title:
        raise ValueError("The naming model returned no title. Retry /name or use /name <name>.")
    from hermes_state import SessionDB

    return SessionDB.sanitize_title(title)


def rename_session(db, session_id: str, messages: list[dict], *, main_runtime: dict | None = None) -> str:
    previous = db.get_session_title(session_id)
    title = generate_session_name(messages, main_runtime=main_runtime)
    if db.get_session_title(session_id) != previous:
        raise ValueError("This session was renamed while the model was working; keeping that name.")
    if not db.set_session_title(session_id, title):
        raise ValueError("The session could not be renamed.")
    return db.get_session_title(session_id) or title


def terminal_label(title: str) -> str:
    # Strip whole ANSI/OSC frames before controls, so a model cannot set a clipboard or inject
    # extra terminal commands. Unicode names remain usable on Linux, macOS and WSL terminals.
    clean = re.sub(r"\x1b\].*?(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]", "", str(title), flags=re.S)
    clean = "".join(c for c in clean if not unicodedata.category(c).startswith("C"))
    return " ".join(clean.split())[:120]


def sync_terminal_title(cli) -> None:
    getter = getattr(cli, "_get_status_bar_session_title", None)
    if getter is not None:
        title = getter()
    else:
        db = getattr(cli, "_session_db", None)
        title = getattr(cli, "_pending_title", None) or (db.get_session_title(cli.session_id) if db else "")
    label = terminal_label(title or f"{Path.cwd().name} · {cli.session_id[:8]}")
    label = f"Hermes — {label}"
    if label == getattr(cli, "_native_terminal_title", None):
        return
    cli._app.output.set_title(label)
    cli._app.output.flush()
    cli._native_terminal_title = label


def rename_cli(cli, command: str) -> None:
    from cli import _cprint
    from agent.memory_provider import spawn_context_thread

    arg = command.split(maxsplit=1)
    if len(arg) > 1:
        cli._cmd_title("/title " + arg[1])
        cli._invalidate(min_interval=0.0)
        return
    if cli._agent_running:
        _cprint("  Wait for the active turn to finish before /name, or use /name <name>.")
        return
    db, sid = cli._session_db, cli.session_id
    if db is None:
        _cprint("  Session storage is unavailable.")
        return
    messages = [dict(m) for m in cli.conversation_history]
    agent = getattr(cli, "agent", None)
    runtime = {key: getattr(agent, key, None) for key in ("model", "provider", "base_url", "api_key", "api_mode")}

    def run():
        try:
            title = rename_session(db, sid, messages, main_runtime=runtime)
            _cprint(f"  Session name: {title}")
            cli._status_bar_title_checked_at = 0.0
            cli._invalidate(min_interval=0.0)
        except (ValueError, OSError) as exc:
            _cprint(f"  Could not name the session: {exc}")

    _cprint("  Naming this session…")
    spawn_context_thread(run, name="session-rename").start()
