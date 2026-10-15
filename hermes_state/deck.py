"""Session deck mixin for SessionDB: the registry of OPEN sessions (active until explicitly closed).

A deck row is a link to a conversation, keyed by its compression-lineage ROOT so compaction never
orphans it; the live tip is resolved on read. The row outlives every process that ever ran the
session — terminal death, host restart and idle eviction only change ``state`` — and ends only
through :meth:`deck_close`. Encryption at rest comes from state.db itself (SQLCipher)."""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Runtime states the host snapshots. ``dormant`` = open but no process holds it right now.
DECK_STATES = frozenset({"running", "waiting", "idle", "detached", "dormant"})

_ROW_COLUMNS = ("handle, session_id, surface, cwd, group_name, parent_handle, state, opened_at, "
                "last_seen_at, closed_at, close_reason, closed_by")


class SessionDeckMixin:
    """Open-session registry: open / snapshot / close / resolve."""

    def deck_lineage_root(self, session_id: str) -> str:
        """Compression-lineage root of ``session_id`` (itself when it has no row yet)."""
        if not session_id:
            return session_id
        lineage = self.get_compression_lineage(session_id)
        return lineage[0] if lineage else session_id

    def deck_open(self, session_id: str, *, surface: str, cwd: str = "", group: str = "",
                  parent_handle: Optional[int] = None) -> int:
        """Register ``session_id`` as open and return its handle. Idempotent per conversation: a
        compression continuation or a re-open of a closed row keeps the original handle."""
        root = self.deck_lineage_root(session_id)
        now = time.time()

        # Select-then-write, not an upsert: ON CONFLICT still burns an AUTOINCREMENT value, which
        # would leave gaps in the human-facing handle sequence on every re-open.
        def _do(conn):
            row = conn.execute("SELECT handle FROM deck_sessions WHERE session_id = ?", (root,)).fetchone()
            if row is not None:
                conn.execute(
                    "UPDATE deck_sessions SET surface = ?, cwd = CASE WHEN ? != '' THEN ? ELSE cwd END,"
                    " last_seen_at = ?, closed_at = NULL, close_reason = NULL, closed_by = NULL WHERE handle = ?",
                    (surface, cwd or "", cwd or "", now, row[0]))
                return row[0]
            return conn.execute(
                "INSERT INTO deck_sessions (session_id, surface, cwd, group_name, parent_handle, state,"
                " opened_at, last_seen_at) VALUES (?, ?, ?, ?, ?, 'idle', ?, ?)",
                (root, surface, cwd or "", group or "", parent_handle, now, now)).lastrowid

        return int(self._execute_write(_do))

    def deck_snapshot(self, states: Iterable[Tuple[str, str]]) -> int:
        """Record the runtime state of open sessions: ``(session_id, state)`` pairs, any lineage id.
        One write transaction for the whole batch; closed rows are never touched. Returns rows updated."""
        pairs = [(self.deck_lineage_root(sid), state) for sid, state in states if sid and state in DECK_STATES]
        if not pairs:
            return 0
        now = time.time()

        def _do(conn):
            return sum(conn.execute(
                "UPDATE deck_sessions SET state = ?, last_seen_at = ? WHERE session_id = ? AND closed_at IS NULL",
                (state, now, root)).rowcount for root, state in pairs)

        return int(self._execute_write(_do) or 0)

    def deck_close(self, handle: int, *, reason: str, closed_by: str = "") -> bool:
        """Close an open row; False when it is unknown or already closed (first close wins)."""
        return self._write_rowcount(
            "UPDATE deck_sessions SET closed_at = ?, close_reason = ?, closed_by = ?, state = 'dormant'"
            " WHERE handle = ? AND closed_at IS NULL",
            (time.time(), reason, closed_by, int(handle))) > 0

    def _deck_project(self, row) -> Dict[str, Any]:
        entry = dict(row)
        root = entry["session_id"]
        tip = self.get_compression_tip(root) or root
        meta = self.get_session(tip) or {}
        entry.update(
            tip_session_id=tip,
            title=meta.get("title") or "",
            model=meta.get("model") or "",
            source=meta.get("source") or entry["surface"],
            message_count=int(meta.get("message_count") or 0),
            started=bool(meta),
            last_active=float(meta.get("last_activity_at") or meta.get("started_at") or entry["last_seen_at"]),
            estimated_cost_usd=meta.get("estimated_cost_usd"),
        )
        return entry

    def deck_rows(self, *, include_closed: bool = False) -> List[Dict[str, Any]]:
        """Open rows (optionally closed ones too), handle order, each projected with its live tip."""
        where = "" if include_closed else " WHERE closed_at IS NULL"
        rows = self._read_all(f"SELECT {_ROW_COLUMNS} FROM deck_sessions{where} ORDER BY handle")
        return [self._deck_project(r) for r in rows]

    def deck_row(self, handle: int) -> Optional[Dict[str, Any]]:
        row = self._read_one(f"SELECT {_ROW_COLUMNS} FROM deck_sessions WHERE handle = ?", (int(handle),))
        return self._deck_project(row) if row is not None else None

    def deck_row_for_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """The row owning ``session_id``'s conversation (any id along its compression lineage)."""
        row = self._read_one(f"SELECT {_ROW_COLUMNS} FROM deck_sessions WHERE session_id = ?",
                             (self.deck_lineage_root(session_id),))
        return self._deck_project(row) if row is not None else None

    def deck_open_count(self) -> int:
        row = self._read_one("SELECT COUNT(*) FROM deck_sessions WHERE closed_at IS NULL")
        return int(row[0]) if row else 0

    def deck_prune_unused(self, *, older_than_s: float, keep: Iterable[str] = ()) -> List[int]:
        """Drop open rows whose conversation never produced a stored session (opened, never prompted)
        and has not been seen for ``older_than_s``. ``keep`` = roots a live process still holds."""
        cutoff = time.time() - older_than_s
        kept = set(keep)

        def _do(conn):
            rows = conn.execute(
                "SELECT d.handle, d.session_id FROM deck_sessions d LEFT JOIN sessions s ON s.id = d.session_id"
                " WHERE d.closed_at IS NULL AND s.id IS NULL AND d.last_seen_at < ?", (cutoff,)).fetchall()
            doomed = [int(r[0]) for r in rows if r[1] not in kept]
            conn.executemany("DELETE FROM deck_sessions WHERE handle = ?", [(h,) for h in doomed])
            return doomed

        return list(self._execute_write(_do) or [])
