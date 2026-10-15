"""Restore open deck conversations into native terminals, without replaying work.

The small --attach entrypoint is an authenticated host client, not an agent: no vault key,
provider credentials or config decryption is needed in each spawned terminal.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@dataclass(frozen=True)
class RestoreTarget:
    ref: str
    profile: str
    handle: int
    session_id: str
    cwd: str
    title: str


def plan_restore(rows: list[dict], *, include_attached: bool = False) -> list[RestoreTarget]:
    return [RestoreTarget(row["ref"], row["profile"], row["handle"], row["tip_session_id"],
                          row["cwd"], row.get("title") or "")
            for row in rows if row.get("closed_at") is None and (include_attached or not row.get("attached"))]


def attach_client(args) -> int:
    from hermes_cli.session_host import HostClient, HostUnavailable, host_ws_url
    from tools.environments.local import build_subprocess_env

    url = host_ws_url()
    if not url:
        raise HostUnavailable("The session host stopped. Run hermes deck restore again.")
    with HostClient(url) as client:
        row = client.call("deck.resolve", {"target": args.attach, "profile": args.profile})["session"]
    cwd = row["cwd"] or str(Path.home())
    if not Path(cwd).is_dir():
        raise FileNotFoundError(f"Session {row['ref']} working directory no longer exists: {cwd}")
    env = build_subprocess_env(scrub_secrets=True, inherit_profile_home=False)
    env.update(HERMES_TUI_GATEWAY_URL=url, HERMES_TUI_DECK="1", HERMES_TUI_PROFILE=row["profile"],
               HERMES_TUI_RESUME=row["tip_session_id"], HERMES_TUI_CWD=cwd, HERMES_CWD=cwd,
               NODE_ENV="production")
    # No prompt/query is sent: resume restores history and waits for the user.
    # This is the interactive terminal session, not a bounded launcher probe.
    return subprocess.call(json.loads(args.client_command), cwd=cwd, env=env)  # health: allow HX006 -- the user's attached terminal runs until they exit


def cmd_restore(args) -> int:
    from hermes_cli.session_host import connect
    from hermes_platform.resolver.terminals import resolve_terminal, terminal_command

    params = {"profiles": [args.profile]} if args.profile else {}
    with connect() as client:
        rows = client.call("deck.list", params)["sessions"]
        targets = plan_restore(rows, include_attached=args.include_attached)
        if not targets:
            print("No detached open sessions to restore.")
            return 0
        if args.dry_run:
            print(json.dumps([vars(target) for target in targets], indent=2))
            return 0
        name, executable = resolve_terminal(args.terminal, platform=sys.platform)
        from hermes_cli.main_tui_launch import _make_tui_argv

        command, _ = _make_tui_argv(Path(__file__).resolve().parents[1] / "ui-tui", False)
        started, failures = set(), []
        for target in targets:
            if target.cwd and not Path(target.cwd).is_dir():
                failures.append(f"{target.ref}: working directory no longer exists: {target.cwd}")
                continue
            argv = [sys.executable, str(Path(__file__).resolve()), "--attach", target.ref,
                    "--profile", target.profile, "--client-command", json.dumps(command)]
            native = terminal_command(name, argv, platform=sys.platform)
            native[0] = executable
            try:
                child = subprocess.Popen(native, stdin=subprocess.DEVNULL, start_new_session=True)
                if name in {"Terminal", "tmux"} and child.wait(timeout=15) != 0:
                    failures.append(f"{target.ref}: terminal launcher failed")
                else:
                    started.add((target.profile, target.session_id))
            except (OSError, subprocess.TimeoutExpired) as exc:
                failures.append(f"{target.ref}: {exc}")
        deadline = time.monotonic() + args.wait
        pending = set(started)
        while pending and time.monotonic() < deadline:
            live = client.call("deck.list", params)["sessions"]
            pending -= {(row["profile"], row["tip_session_id"]) for row in live if row.get("attached")}
            if pending:
                time.sleep(0.2)
        for profile, sid in pending:
            failures.append(f"{profile}/{sid}: window launched but the session did not attach within {args.wait:g}s")
        print(f"Restored {len(started) - len(pending)} session terminal(s).")
        if name == "tmux" and started:
            print("Use tmux list-sessions and tmux attach -t <session> to open them.")
        for failure in failures:
            print(f"Could not restore {failure}", file=sys.stderr)
        return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attach", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--client-command", required=True)
    args = parser.parse_args()
    try:
        return attach_client(args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Session restore failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
