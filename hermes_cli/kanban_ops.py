"""Dispatcher and maintenance verbs for ``hermes kanban``: ``dispatch``,
``tail``/``watch`` event streaming,
``gc`` and ``repair``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli import kanban_db_workspace as kbw
from hermes_cli.kanban_output import _err, _fmt_ts, _print_json


def _kanban_config() -> dict:
    """``config.yaml`` ``kanban:`` section, or ``{}`` when config can't be loaded."""
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        return (cfg.get("kanban", {}) if isinstance(cfg, dict) else {}) or {}
    except Exception:
        return {}


def _poll_loop(interval: float, tick) -> int:
    """Run ``tick()`` every ``interval`` seconds (floor 0.1) until Ctrl-C."""
    try:
        while True:
            tick()
            time.sleep(max(0.1, interval))
    except KeyboardInterrupt:
        print("\n(stopped)")
        return 0


def _cmd_tail(args: argparse.Namespace) -> int:
    last_id = 0
    print(f"Tailing events for {args.task_id}. Ctrl-C to stop.")

    def tick():
        nonlocal last_id
        with kbc.connect_closing() as conn:
            events = kb.list_events(conn, args.task_id)
        for e in events:
            if e.id > last_id:
                pl = f" {e.payload}" if e.payload else ""
                print(f"[{_fmt_ts(e.created_at)}] {e.kind}{pl}", flush=True)
                last_id = e.id

    return _poll_loop(args.interval, tick)


def _cmd_dispatch(args: argparse.Namespace) -> int:
    # Honour kanban.default_assignee, kanban.max_in_progress,
    # kanban.max_in_progress_per_profile and kanban.max_spawn with the same
    # semantics on every explicit local pass.
    from hermes_cli.config import load_config
    _cfg = load_config()
    _kanban_cfg = _cfg.get("kanban", {}) if isinstance(_cfg, dict) else {}
    default_assignee = (_kanban_cfg.get("default_assignee") or "").strip() or None
    max_in_progress_per_profile = kbd._positive_int(
        _kanban_cfg.get("max_in_progress_per_profile"), None
    )
    # Memory-derived default when unset.
    max_in_progress = kbd.resolve_max_in_progress(
        kbd._positive_int(_kanban_cfg.get("max_in_progress"), None)
    )
    # CLI --max is the more explicit signal, so it wins over kanban.max_spawn.
    cli_max = getattr(args, "max", None)
    max_spawn = (
        cli_max if cli_max is not None else kbd._positive_int(_kanban_cfg.get("max_spawn"), None)
    )
    stale_timeout = kbd._positive_int(_kanban_cfg.get("dispatch_stale_timeout_seconds"), 0)
    reconcile = bool(_kanban_cfg.get("reconcile_orphans", True))
    failure_limit = kbd._positive_int(
        getattr(args, "failure_limit", None),
        kbd._positive_int(_kanban_cfg.get("failure_limit"), kbd.DEFAULT_FAILURE_LIMIT),
    )
    with kbc.connect_closing() as conn:
        res = kbd.dispatch_once(
            conn,
            dry_run=args.dry_run,
            max_spawn=max_spawn,
            max_in_progress=max_in_progress,
            failure_limit=failure_limit,
            stale_timeout_seconds=stale_timeout,
            reconcile_orphans=reconcile,
            default_assignee=default_assignee,
            max_in_progress_per_profile=max_in_progress_per_profile,
        )
    if getattr(args, "json", False):
        _print_json({
            **{k: getattr(res, k)
               for k in ("reclaimed", "crashed", "timed_out", "stale", "auto_blocked", "promoted",
                         "reaped_terminal_workers")},
            "spawned": [
                {"task_id": tid, "assignee": who, "workspace": ws} for (tid, who, ws) in res.spawned
            ],
            "skipped_unassigned": res.skipped_unassigned,
            "skipped_nonspawnable": res.skipped_nonspawnable,
            "skipped_per_profile_capped": [
                {"task_id": tid, "assignee": who, "current": current}
                for (tid, who, current) in res.skipped_per_profile_capped
            ],
            "auto_assigned_default": res.auto_assigned_default,
            "respawn_guarded": [
                {"task_id": tid, "reason": reason}
                for (tid, reason) in res.respawn_guarded
            ],
            "rate_limited": res.rate_limited,
            "skipped_locked": res.skipped_locked,
            "memory_pressure": res.memory_pressure,
        }, ascii=True)
        return 0
    print(f"Reclaimed:    {res.reclaimed}")
    if res.reaped_terminal_workers:
        print(f"Reaped workers of finished tasks: {', '.join(res.reaped_terminal_workers)}")
    for label, items in (
        ("Crashed:     ", res.crashed),
        ("Timed out:   ", res.timed_out),
        ("Stale:       ", res.stale),
        ("Auto-blocked:", res.auto_blocked),
    ):
        print(f"{label} {len(items)}")
        if items:
            print(f"  {', '.join(items)}")
    print(f"Promoted:     {res.promoted}")
    print(f"Spawned:      {len(res.spawned)}")
    tag = " (dry)" if args.dry_run else ""
    for tid, who, ws in res.spawned:
        print(f"  - {tid}  ->  {who}  @ {ws or '-'}{tag}")
    if res.auto_assigned_default:
        print(
            f"Auto-assigned to kanban.default_assignee={default_assignee!r}: "
            f"{', '.join(res.auto_assigned_default)}"
        )
    if res.skipped_unassigned:
        print(f"Skipped (unassigned): {', '.join(res.skipped_unassigned)}")
    for tid, who, current in res.skipped_per_profile_capped:
        print(f"Deferred ({who} at per-profile cap, {current} running): {tid}")
    if res.skipped_nonspawnable:
        print(
            f"Skipped (non-spawnable assignee — terminal lane, OK): "
            f"{', '.join(res.skipped_nonspawnable)}"
        )
    for tid, reason in res.respawn_guarded:
        print(f"Guarded ({reason}): {tid}")
    if res.rate_limited:
        print(f"Rate-limited (released to ready, no failure counted): {', '.join(res.rate_limited)}")
    if res.skipped_locked:
        print("Skipped: another dispatcher holds this board's lock (no writes this tick)")
    if res.memory_pressure:
        print(f"Memory pressure {res.memory_pressure}: new workers restricted this tick")
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    """Live-stream task_events to the terminal."""
    kinds = {k.strip() for k in args.kinds.split(",") if k.strip()} if args.kinds else None
    print(f"Watching kanban events (initial board '{kb.get_current_board()}'). Ctrl-C to stop.", flush=True)
    # Seed cursor at the latest id so we don't replay history.
    with kbc.connect_closing() as conn:
        cursor = int(conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM task_events").fetchone()["m"])

    def tick():
        nonlocal cursor
        with kbc.connect_closing() as conn:
            rows = conn.execute(
                "SELECT e.id, e.task_id, e.kind, e.payload, e.created_at,        t.assignee, "
                "t.tenant FROM task_events e LEFT JOIN tasks t ON t.id = e.task_id WHERE e.id > ? "
                "ORDER BY e.id ASC LIMIT 200",
                (cursor,),
            ).fetchall()
        for r in rows:
            cursor = max(cursor, int(r["id"]))
            if (kinds and r["kind"] not in kinds) or (args.assignee and r["assignee"] != args.assignee) \
                    or (args.tenant and r["tenant"] != args.tenant):
                continue
            try:
                payload = json.loads(r["payload"]) if r["payload"] else None
            except Exception:
                payload = None
            pl = f" {payload}" if payload else ""
            print(
                f"[{_fmt_ts(kb._to_epoch(r['created_at']))}] {r['task_id']:10s} "
                f"{r['kind']:18s} (@{r['assignee'] or '-'}){pl}",
                flush=True,
            )

    return _poll_loop(args.interval, tick)


def _cmd_gc(args: argparse.Namespace) -> int:
    """Remove archived tasks' scratch workspaces, old events, and old worker logs."""
    import shutil
    event_days = getattr(args, "event_retention_days", 30)
    log_days = getattr(args, "log_retention_days", 30)
    if event_days < 0 or log_days < 0:
        return _err("kanban gc: retention days must be >= 0 (0 disables that sweep)", 2)
    scratch_root = kb.workspaces_root()
    removed_ws = 0
    with kbc.connect_closing() as conn:
        rows = conn.execute(
            "SELECT id, workspace_kind, workspace_path, branch_name FROM tasks "
            "WHERE status = 'archived'"
        ).fetchall()
        for row in rows:
            if row["workspace_kind"] == "worktree":
                # Backstop for worktrees that escaped the completion/archive hook.
                # Same safety predicate: only clean, fully-pushed worktrees go.
                wt_path = row["workspace_path"]
                if wt_path and Path(wt_path).is_dir():
                    if kbw._defer_shared_worktree_cleanup(conn, row["id"], wt_path):
                        continue
                    kbw._cleanup_worktree_workspace(row["id"], wt_path, row["branch_name"])
                    if not Path(wt_path).is_dir():
                        removed_ws += 1
                continue
            if row["workspace_kind"] != "scratch":
                continue
            path = Path(row["workspace_path"] or (scratch_root / row["id"]))
            # Same containment predicate as completion cleanup (#28818): strictly below a
            # managed root, never the root itself (which holds every task's scratch dir).
            # Cheap existence/symlink check first: most rows were already cleaned at
            # completion, and rmtree refuses a symlink (so it must not be counted).
            if not path.is_dir() or path.is_symlink() or not kbw._is_managed_scratch_path(path):
                continue
            if kbw._defer_shared_workspace_cleanup(conn, row["id"], path):
                continue
            shutil.rmtree(path, ignore_errors=True)
            if not path.exists():
                removed_ws += 1

    removed_events = 0
    if event_days:
        with kbc.connect_closing() as conn:
            removed_events = kb.gc_events(conn, older_than_seconds=event_days * 24 * 3600)
    removed_logs = kb.gc_worker_logs(older_than_seconds=log_days * 24 * 3600) if log_days else 0
    print(f"GC complete: {removed_ws} workspace(s), "
          f"{removed_events} event row(s), {removed_logs} log file(s) removed")
    return 0


def _cmd_repair(args: argparse.Namespace) -> int:
    """Integrity check + narrow index-REINDEX auto-repair. Dispatched BEFORE
    the auto ``kb.init_db()`` (init refuses corrupt DBs). Exit 0 = healthy /
    repaired / no DB file, 1 = still corrupt."""
    try:
        report = kbc.repair_db()
    except Exception as exc:  # locked/busy probe, unexpected I/O
        return _err(f"kanban repair: {exc}")

    if getattr(args, "json", False):
        _print_json({
            "status": report.status,
            "db_path": str(report.db_path),
            "messages": report.messages,
            "post_repair_messages": report.post_repair_messages,
            "backup_path": str(report.backup_path) if report.backup_path else None,
            "reindexed": report.reindexed,
        }, ascii=True)
        return 0 if report.status in {"ok", "repaired", "missing"} else 1

    if report.status == "missing":
        print(f"No kanban DB at {report.db_path} — nothing to repair.")
        return 0
    if report.status == "ok":
        print(f"{report.db_path}: integrity_check ok — no repair needed.")
        return 0
    if report.status == "repaired":
        print(f"{report.db_path}: repaired.")
        print(f"  reindexed: {', '.join(report.reindexed)}")
        if report.backup_path:
            print(f"  pre-repair backup: {report.backup_path}")
        print("  integrity_check now ok.")
        return 0
    # still corrupt
    def err(line: str) -> None:
        print(line, file=sys.stderr)

    err(f"{report.db_path}: CORRUPT.")
    for line in (report.messages or [])[:10]:
        err(f"  {line}")
    if report.reindexed:
        err(f"  REINDEX ({', '.join(report.reindexed)}) attempted but integrity_check is still failing:")
        for line in (report.post_repair_messages or [])[:10]:
            err(f"    {line}")
    else:
        err("  Not an index-only failure — automatic REINDEX repair does not apply (fail-closed).")
    if report.backup_path:
        err(f"  corrupt copy quarantined at: {report.backup_path}")
    err(
        "  Recover manually (copy kanban.db aside FIRST, then run "
        "`sqlite3 <copy> \".recover\"` into a fresh file — never against "
        "the live path, a WAL-reset-vulnerable sqlite3 CLI can corrupt it "
        "further) or move the file aside to start a new board."
    )
    return 1
