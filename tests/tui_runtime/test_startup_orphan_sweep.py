"""Native startup reclaims provably orphaned rows without making them unresumable."""

from __future__ import annotations

import io
import time
import types

from hermes_state import SessionDB
from tui_runtime import entry, server
from hermes_cli import model_switch_providers


IDLE_S = 6 * 3600


def _seed_session(db, session_id, *, source, last_active, started_at=None):
    db.create_session(session_id, source=source)
    db.append_message(session_id, role="user", content="hello")
    with db._lock:
        db._conn.execute(
            "UPDATE sessions SET started_at = ? WHERE id = ?",
            (last_active if started_at is None else started_at, session_id),
        )
        db._conn.execute(
            "UPDATE messages SET timestamp = ? WHERE session_id = ?",
            (last_active, session_id),
        )
        db._conn.commit()


class TestSweepOrphanedSessionRows:
    def test_ends_stale_tui_desktop_and_subagent(self, monkeypatch, tmp_path):
        db = SessionDB(tmp_path / "state.db")
        stale = time.time() - 8 * 3600
        _seed_session(db, "stale-tui", source="tui", last_active=stale)
        _seed_session(db, "stale-desktop", source="desktop", last_active=stale)
        _seed_session(db, "stale-sub", source="subagent", last_active=stale)
        monkeypatch.setattr(server, "_get_db", lambda: db)
        monkeypatch.setattr(server, "_SESSION_TTL_S", float(IDLE_S))
        monkeypatch.setattr(server, "_sessions", {})

        swept = server._sweep_orphaned_session_rows()

        assert sorted(swept) == ["stale-desktop", "stale-sub", "stale-tui"]
        for sid in ("stale-tui", "stale-desktop", "stale-sub"):
            row = db.get_session(sid)
            assert row["ended_at"] is not None
            assert row["end_reason"] == "startup_orphan_reap"

    def test_swept_row_stays_resumable(self, monkeypatch, tmp_path):
        """A stranded 'active' row (ended_at NULL, no live runtime) is swept
        AND still resumable afterward (#65194 salvage requirement).

        The durable row and transcript stay addressable by native resume; a
        startup sweep closes phantom liveness, not the user's conversation.
        """
        db = SessionDB(tmp_path / "state.db")
        stale = time.time() - 8 * 3600
        _seed_session(db, "stranded-tui", source="tui", last_active=stale)
        with db._lock:
            db._conn.execute(
                "UPDATE sessions SET session_key = ? WHERE id = ?",
                ("tui:peer-1", "stranded-tui"),
            )
            db._conn.commit()
        monkeypatch.setattr(server, "_get_db", lambda: db)
        monkeypatch.setattr(server, "_SESSION_TTL_S", float(IDLE_S))
        monkeypatch.setattr(server, "_sessions", {})  # no live runtime

        assert server._sweep_orphaned_session_rows() == ["stranded-tui"]
        row = db.get_session("stranded-tui")
        assert row["ended_at"] is not None
        assert row["end_reason"] == "startup_orphan_reap"

        # The distinct reason is a recoverable accident, not a boundary.
        assert "startup_orphan_reap" in SessionDB.RECOVERABLE_END_REASONS

        assert db.resolve_resume_session_id("stranded-tui") == "stranded-tui"

        # ...and the session.resume path (reopen_session) fully revives it.
        db.reopen_session("stranded-tui")
        revived = db.get_session("stranded-tui")
        assert revived["ended_at"] is None
        assert revived["end_reason"] is None

    def test_spares_fresh_row_with_old_copied_history(self, monkeypatch, tmp_path):
        db = SessionDB(tmp_path / "state.db")
        old_history = time.time() - 8 * 3600
        _seed_session(
            db,
            "fresh-branch",
            source="tui",
            last_active=old_history,
            started_at=time.time(),
        )
        monkeypatch.setattr(server, "_get_db", lambda: db)
        monkeypatch.setattr(server, "_SESSION_TTL_S", float(IDLE_S))
        monkeypatch.setattr(server, "_sessions", {})

        assert server._sweep_orphaned_session_rows() == []
        assert db.get_session("fresh-branch")["ended_at"] is None

    def test_spares_pinned_idle_row(self, monkeypatch, tmp_path):
        """Pinned rows are prune-immune (#68035); the startup sweep must match auto-prune."""
        db = SessionDB(tmp_path / "state.db")
        stale = time.time() - 8 * 3600
        _seed_session(db, "pinned-tui", source="tui", last_active=stale)
        _seed_session(db, "stale-tui", source="tui", last_active=stale)
        db.set_session_pinned("pinned-tui", True)
        monkeypatch.setattr(server, "_get_db", lambda: db)
        monkeypatch.setattr(server, "_SESSION_TTL_S", float(IDLE_S))
        monkeypatch.setattr(server, "_sessions", {})

        assert server._sweep_orphaned_session_rows() == ["stale-tui"]
        assert db.get_session("pinned-tui")["ended_at"] is None
        assert db.get_session("stale-tui")["end_reason"] == "startup_orphan_reap"

    def test_spares_live_in_memory_and_gateway_rows(self, monkeypatch, tmp_path):
        db = SessionDB(tmp_path / "state.db")
        stale = time.time() - 8 * 3600
        _seed_session(db, "resumed-tui", source="tui", last_active=stale)
        _seed_session(db, "gateway-row", source="telegram", last_active=stale)
        _seed_session(db, "recent-tui", source="tui", last_active=time.time() - 30)
        monkeypatch.setattr(server, "_get_db", lambda: db)
        monkeypatch.setattr(server, "_SESSION_TTL_S", float(IDLE_S))
        monkeypatch.setattr(
            server,
            "_sessions",
            {
                "mem-sid": {
                    "agent": types.SimpleNamespace(session_id="resumed-tui"),
                    "session_key": "resumed-tui",
                }
            },
        )

        assert server._sweep_orphaned_session_rows() == []
        for row_id in ("resumed-tui", "gateway-row", "recent-tui"):
            assert db.get_session(row_id)["ended_at"] is None

    def test_leaves_already_ended_rows_untouched(self, monkeypatch, tmp_path):
        db = SessionDB(tmp_path / "state.db")
        stale = time.time() - 8 * 3600
        _seed_session(db, "reaped-tui", source="tui", last_active=stale)
        db.end_session("reaped-tui", "ws_orphan_reap")
        before = db.get_session("reaped-tui")
        monkeypatch.setattr(server, "_get_db", lambda: db)
        monkeypatch.setattr(server, "_SESSION_TTL_S", float(IDLE_S))
        monkeypatch.setattr(server, "_sessions", {})

        assert server._sweep_orphaned_session_rows() == []
        after = db.get_session("reaped-tui")
        assert after["end_reason"] == "ws_orphan_reap"
        assert after["ended_at"] == before["ended_at"]

    def test_zero_ttl_skips_sweep(self, monkeypatch, tmp_path):
        db = SessionDB(tmp_path / "state.db")
        stale = time.time() - 8 * 3600
        _seed_session(db, "stale-tui", source="tui", last_active=stale)
        monkeypatch.setattr(server, "_get_db", lambda: db)
        monkeypatch.setattr(server, "_SESSION_TTL_S", 0.0)
        monkeypatch.setattr(server, "_sessions", {})

        assert server._sweep_orphaned_session_rows() == []
        assert db.get_session("stale-tui")["ended_at"] is None


class TestScheduleStartupOrphanSweep:
    def test_once_per_process_and_ttl_gate(self, monkeypatch):
        started = {"count": 0}

        class _Timer:
            def __init__(self, *a, **k):
                pass

            def start(self):
                started["count"] += 1

        monkeypatch.setattr(server.threading, "Timer", _Timer)
        monkeypatch.setattr(server, "_SESSION_TTL_S", 0.0)

        monkeypatch.setattr(server, "_startup_orphan_sweep_ran", False)
        server._schedule_startup_orphan_sweep()
        assert started["count"] == 0
        assert server._startup_orphan_sweep_ran is False

        monkeypatch.setattr(server, "_SESSION_TTL_S", float(IDLE_S))
        server._schedule_startup_orphan_sweep()
        server._schedule_startup_orphan_sweep()
        assert started["count"] == 1
        assert server._startup_orphan_sweep_ran is True

        monkeypatch.setattr(server, "_SESSION_TTL_S", 0.0)
        monkeypatch.setattr(server, "_startup_orphan_sweep_ran", False)
        server._schedule_startup_orphan_sweep()
        assert started["count"] == 1



class TestEntryAndWsWiring:


    def test_schedule_failure_does_not_break_main(self, monkeypatch):
        def _boom():
            raise RuntimeError("nope")

        monkeypatch.setattr(server, "_schedule_startup_orphan_sweep", _boom)
        monkeypatch.setattr(entry, "ensure_mcp_discovery_started", lambda: None)
        monkeypatch.setattr(entry, "resolve_skin", lambda: "default")
        monkeypatch.setattr(entry.server, "_ensure_skin_watcher", lambda: None)
        monkeypatch.setattr(entry, "_log_exit", lambda reason: None)
        monkeypatch.setattr(entry, "handle_spurious_eof", lambda *a: False)
        monkeypatch.setattr(entry, "write_json", lambda _payload: True)
        monkeypatch.setattr(entry.sys, "stdin", io.StringIO(""))

        monkeypatch.setattr(model_switch_providers, "prewarm_picker_cache_async", lambda: None)

        entry.main()  # must not raise
