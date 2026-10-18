"""Native route restoration/heartbeats and passive legacy-history/profile salvage.

Mixin bound onto ``SessionDB`` via the MRO; built on its ``_read_ctx`` /
``_execute_write`` / ``_write_sql`` / ``_read_all`` primitives."""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from hermes_state.common import (
    _RECOVERABLE_END_REASONS_SQL, _RESET_CHILD_SQL, _RESET_END_REASONS_SQL, _sql_json_extract,
    _sql_session_last_active)

# Log-record parity with the origin module (caplog tests pin "hermes_state").
logger = logging.getLogger("hermes_state")

# Recursive CTE naming a session plus its compression ancestors (rows a
# resume must keep on one routing peer); branch/delegate/tool/reset rows stop it
# (same membership rule as get_compression_lineage / _CHAIN_STEP_SQL, #114271).

# Projection shared by both peer-recovery queries (exact key, then peer tuple).
# A row a *completed handoff* owns stays recoverable whatever end_reason it carries: the source
# interface's teardown runs AFTER ownership moved to the destination and can stamp a terminal reason
# (``cli_close``) before the destination's first reply — the row must survive that race or the
# handed-off leg is orphaned and a fresh empty session is minted in its place. A later DELIBERATE
# close is still fenced the ordinary way: a reset-boundary row ended after this row's last activity
# blocks recovery (the NOT EXISTS below). ``pending``/``failed``/NULL handoffs are untouched, so this
# is strictly "ownership was transferred", never "a handoff was attempted".

_ORPHAN_DONOR_COLUMNS = (
    "d.id, d.session_key, d.chat_id, d.chat_type, d.thread_id, "
    "d.user_id, d.origin_json, d.display_name, d.end_reason"
)
_ORPHANS_SQL = f"""
                SELECT o.id, o.source, o.user_id, o.started_at,
                       o.parent_session_id, o.profile_name,
                       {_sql_session_last_active("o")} AS last_active,
                       (SELECT COUNT(*) FROM messages m
                         WHERE m.session_id = o.id) AS message_count
                FROM sessions o
                WHERE o.session_key IS NULL
                  AND EXISTS (SELECT 1 FROM messages m
                               WHERE m.session_id = o.id)
                  AND COALESCE(o.source, '') != 'tool'
                  AND {_sql_json_extract('o.model_config', '$._branched_from')} IS NULL
                  AND {_sql_json_extract('o.model_config', '$._delegate_from')} IS NULL
                ORDER BY o.started_at ASC
                """
# Donor profile fence: the donor's effective profile (NULL reads as the store's own, the same convention
# the peer-tuple recovery predicate uses) must equal the orphan's. A sibling profile's keyed row in a
# shared legacy store would otherwise stamp its ``agent:<other>:`` routing identity onto this profile's
# orphan. Stores outside the profile tree derive no owner and keep the historical unfenced behavior.
_ORPHAN_DONOR_PROFILE_FENCE_SQL = "AND (? IS NULL OR COALESCE(d.profile_name, ?) = ?)"
_ORPHAN_LINEAGE_DONOR_SQL = f"""
                        SELECT {_ORPHAN_DONOR_COLUMNS}
                        FROM sessions d
                        WHERE d.id = ?
                          AND d.session_key IS NOT NULL
                          AND COALESCE(d.source, '') = COALESCE(?, '')
                          {_ORPHAN_DONOR_PROFILE_FENCE_SQL}
                        """
_ORPHAN_CONTIGUITY_DONORS_SQL = f"""
                        SELECT {_ORPHAN_DONOR_COLUMNS}, {_sql_session_last_active("d")} AS last_active
                        FROM sessions d
                        WHERE d.session_key IS NOT NULL
                          AND d.id != ?
                          AND COALESCE(d.source, '') = COALESCE(?, '')
                          AND (COALESCE(d.user_id, '') = ''
                               OR COALESCE(?, '') = ''
                               OR d.user_id = ?)
                          AND {_sql_session_last_active("d")} BETWEEN ? AND ?
                          AND {_sql_session_last_active("d")} < ?
                          {_ORPHAN_DONOR_PROFILE_FENCE_SQL}
                        ORDER BY last_active DESC
                        LIMIT 2
                        """
_OPTIONAL_TABLE_NAMES = (
    "telegram_dm_topic_mode", "telegram_dm_topic_bindings", "delivery_obligations")


def _optional_table_columns(conn) -> dict[str, set[str]]:
    """Live column sets of the lazily-created tables that exist (``{}`` when none do).

    ``apply_telegram_topic_migration`` runs only on explicit ``/topic`` opt-in, so a store
    can hold supported v1/v2 tables without ``profile_name`` for its whole life; likewise the
    delivery ledger adds ``delivery_obligations.adapter_profile`` only when a gateway opens
    it. Identity settlement must gate its column SQL on the column actually being there
    rather than on table existence, and must not force those migrations (#113757).
    """
    existing = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?, ?, ?)",
        _OPTIONAL_TABLE_NAMES)}
    return {
        table: {row[1] for row in conn.execute(f"PRAGMA table_info('{table}')")}
        for table in _OPTIONAL_TABLE_NAMES if table in existing
    }




class SessionLegacyMixin:
    """Routing index, session peers/orphans, hygiene streaks, heartbeats, handoffs."""






    def list_never_active_keyed_sessions(self, *, older_than_days: float) -> list[dict[str, Any]]:
        """Keyed, still-open rows with no evidence of a single turn (no messages, tokens, tool/API calls,
        activity, or title): leaked fixtures or chats routed but never answered. Safe to drop — the gateway
        mints a fresh session on the next message. Needs its own selector because ``bulk prune``/``archive``
        are pinned to ``ended_at IS NOT NULL``. ``pinned``/``archived`` = explicit keep intent.

        That is exactly the shape of a leaked test fixture (#82770) — and also of a chat that was routed but
        never answered.
        """
        if older_than_days < 0:
            raise ValueError(
                f"older_than_days must be >= 0, got {older_than_days!r}: a negative "
                "retention builds a future cutoff that matches every never-active keyed row.")
        cutoff = time.time() - (float(older_than_days) * 86400.0)
        rows = self._read_all(
            """
            SELECT s.id, s.session_key, s.source, s.chat_id,
                   s.chat_type, s.user_id, s.started_at
              FROM sessions s
             WHERE s.session_key IS NOT NULL
               AND s.ended_at IS NULL
               AND s.title IS NULL
               AND s.last_activity_at IS NULL
               AND COALESCE(s.message_count, 0) = 0
               AND COALESCE(s.tool_call_count, 0) = 0
               AND COALESCE(s.api_call_count, 0) = 0
               AND COALESCE(s.input_tokens, 0) = 0
               AND COALESCE(s.output_tokens, 0) = 0
               AND COALESCE(s.pinned, 0) = 0
               AND COALESCE(s.archived, 0) = 0
               AND s.started_at IS NOT NULL
               AND s.started_at < ?
               AND NOT EXISTS (
                       SELECT 1 FROM messages m WHERE m.session_id = s.id
                   )
             ORDER BY s.started_at
            """,
            (cutoff,),
        )
        return [dict(r) for r in rows]


    def _routing_entries_for_sessions(self, session_ids: set[str]) -> list[tuple[str, str, str]]:
        """``(scope, session_key, session_id)`` of ``gateway_routing`` rows pointing at any of
        *session_ids*; the target id lives only inside ``entry_json``, so matching is in Python."""
        found: list[tuple[str, str, str]] = []
        if not session_ids:
            return found
        for row in self._read_all("SELECT scope, session_key, entry_json FROM gateway_routing"):
            try:
                entry = json.loads(row["entry_json"] or "{}")
            except (ValueError, TypeError, RecursionError):  # malformed / non-text / over-nested entry
                continue
            if isinstance(entry, dict) and entry.get("session_id") in session_ids:
                found.append((row["scope"], row["session_key"], entry["session_id"]))
        return found

    def _delete_routing_entries_for_sessions(self, session_ids: set[str]) -> int:
        """Drop ``gateway_routing`` rows pointing at any of *session_ids*."""
        doomed = [(scope, key) for scope, key, _ in self._routing_entries_for_sessions(session_ids)]
        if not doomed:
            return 0
        self._write_sql("DELETE FROM gateway_routing WHERE scope = ? AND session_key = ?", doomed, many=True)
        return len(doomed)

    def prune_never_active_keyed_sessions(
        self, *, older_than_days: float, sessions_dir: Optional[Path] = None,
        deleted_ids: Optional[list] = None) -> tuple[int, int, int]:
        """Delete never-active keyed rows and the routing entries naming them; returns
        ``(sessions_deleted, routing_entries_deleted, sessions_skipped)``. Deletion goes through
        :meth:`delete_sessions` (one transaction; delegate cascade, FTS, transcripts).
        Rows under a live turn lease or compression lock are skipped (#123583): the
        never-active predicate reads committed state, so a keyed row whose first turn lease
        is already held — its messages not yet flushed — would otherwise be deleted
        mid-turn. Routing entries are dropped only after the rows commit, for every
        candidate except the guarded ones: rows that vanished concurrently are gone too,
        and a stale entry outliving its target would have the gateway resume a
        nonexistent id. The row sweep is one transaction; routing entries are dropped in a
        second write after it commits. ``sessions_skipped`` counts only the guarded rows.
        ``deleted_ids`` (the owning gateway) receives every candidate id that was not kept."""
        candidates = self.list_never_active_keyed_sessions(older_than_days=older_than_days)
        # A fresh local reset tip is its conversation's current target, not a never-active chat:
        # the conversation holds the earlier segments' history and the owner's queued work.
        from hermes_state.local import owned_lineage_ids
        with self._read_ctx() as conn:
            ids = {str(row["id"]) for row in candidates if owned_lineage_ids(conn, str(row["id"])) == [str(row["id"])]}
        if not ids:
            return (0, 0, 0)
        skipped: list[str] = []
        # ``delete_sessions`` retires the deleted rows' routing entries inside its own transaction
        # (``retire_routes``), so count what pointed at the candidates before it commits; the
        # follow-up sweep then only catches entries a concurrent writer re-added.
        routed_before = self._routing_entries_for_sessions(ids)
        deleted = self.delete_sessions(
            list(ids), sessions_dir=sessions_dir, exclude_active_write_guards=True, skipped_ids=skipped)
        kept = set(skipped)
        self._delete_routing_entries_for_sessions(ids - kept)
        routing_deleted = sum(1 for _, _, sid in routed_before if sid not in kept)
        if deleted_ids is not None:
            deleted_ids.extend(sorted(ids - kept))
        return (deleted, routing_deleted, len(skipped))



    def find_orphaned_gateway_sessions(self, *, max_gap_s: Optional[float] = None) -> list[dict[str, Any]]:
        """Report message-bearing rows that lost their routing identity (messages, no ``session_key``).
        Adoptable only when exactly one keyed predecessor can be named: ``lineage`` (``parent_session_id``
        is a keyed row of the same source; no time window) or ``contiguity`` (exactly one keyed same-source
        row with compatible ``user_id`` fell quiet within *max_gap_s* of the orphan's start and is older
        than its last activity). Ambiguity is reported ``adoptable=False`` with a reason, never guessed —
        mis-adopting splices one person's conversation into another's chat. The donor must share the
        orphan's effective profile (NULL reads as this store's own): a sibling profile's keyed row in a
        shared legacy store must never stamp its routing identity here. Branch/delegate/tool rows are
        excluded: unkeyed by design, not damage."""
        gap = self._ORPHAN_ADOPTION_MAX_GAP_S if max_gap_s is None else float(max_gap_s)
        owner = self._own_profile_name()
        records: list[dict[str, Any]] = []
        with self._read_ctx() as conn:
            for orphan in conn.execute(_ORPHANS_SQL).fetchall():
                donor = None
                reason = ""
                orphan_owner = orphan["profile_name"] or owner
                if orphan["parent_session_id"]:
                    evidence = "lineage"
                    donor = conn.execute(
                        _ORPHAN_LINEAGE_DONOR_SQL,
                        (orphan["parent_session_id"], orphan["source"], owner, owner, orphan_owner),
                    ).fetchone()
                    if donor is None:
                        reason = "parent session carries no gateway identity of this source"
                else:
                    evidence = "contiguity"
                    started = orphan["started_at"] or 0
                    candidates = conn.execute(
                        _ORPHAN_CONTIGUITY_DONORS_SQL,
                        (orphan["id"], orphan["source"], orphan["user_id"], orphan["user_id"],
                         started - gap, started + gap, orphan["last_active"],
                         owner, owner, orphan_owner),
                    ).fetchall()
                    if not candidates:
                        reason = f"no keyed predecessor fell quiet within {gap:.0f}s of this session's start"
                    elif len(candidates) > 1:
                        reason = "ambiguous: more than one keyed predecessor matches this window"
                    else:
                        donor = candidates[0]
                records.append({
                    "orphan_id": orphan["id"], "source": orphan["source"],
                    "message_count": orphan["message_count"], "started_at": orphan["started_at"],
                    "last_active": orphan["last_active"], "donor_id": donor["id"] if donor else None,
                    "session_key": donor["session_key"] if donor else None,
                    "evidence": evidence if donor else "", "adoptable": donor is not None, "reason": reason})
        # Two unkeyed successors claiming one predecessor: at most one continues that chat.
        contested = {
            r["donor_id"] for r in records
            if r["adoptable"] and sum(1 for x in records if x["donor_id"] == r["donor_id"]) > 1
        }
        for record in records:
            if record["donor_id"] in contested:
                record["adoptable"] = False
                record["reason"] = "ambiguous: more than one unkeyed session claims this predecessor"
        return records

    def adopt_orphaned_gateway_session(self, orphan_id: str, donor_id: str) -> bool:
        """Stamp *orphan_id* with *donor_id*'s routing identity, retire *donor_id*.
        Re-verifies the pair inside the write txn so a concurrent gateway that healed
        either row makes this a no-op. Non-NULL orphan columns are preserved."""
        if not orphan_id or not donor_id or orphan_id == donor_id:
            return False
        owner = self._own_profile_name()
        def _do(conn):
            donor = conn.execute(
                "SELECT session_key, chat_id, chat_type, thread_id, user_id, "
                "origin_json, display_name, source, profile_name FROM sessions WHERE id = ?",
                (donor_id,),
            ).fetchone()
            orphan = conn.execute(
                "SELECT session_key, source, profile_name FROM sessions WHERE id = ?",
                (orphan_id,)).fetchone()
            if (donor is None or orphan is None or not donor["session_key"] or orphan["session_key"]
                    or (donor["source"] or "") != (orphan["source"] or "")
                    or (owner is not None
                        and (donor["profile_name"] or owner) != (orphan["profile_name"] or owner))):
                return False
            # Belt-and-suspenders for gateway routing metadata (#59527): the gateway re-records the peer on
            # the child after rotation (d5b4879d4), but a hard crash between child creation and that write
            # leaves the child row without origin columns, so ``find_latest_gateway_session_for_peer`` can't
            # recover the mapping on restart. Inherit them from the parent at creation time — but ONLY for
            # compression forks (parent already ended with end_reason='compression'). Delegate/subagent
            # children are spawned while the parent is still live and must NOT inherit routing keys, or peer
            # recovery could repoint gateway traffic into a subagent's session.
            conn.execute(
                """UPDATE sessions
                      SET session_key = ?,
                          chat_id = COALESCE(chat_id, ?),
                          chat_type = COALESCE(chat_type, ?),
                          thread_id = COALESCE(thread_id, ?),
                          user_id = COALESCE(user_id, ?),
                          origin_json = COALESCE(origin_json, ?),
                          display_name = COALESCE(display_name, ?),
                          parent_session_id = COALESCE(parent_session_id, ?)
                    WHERE id = ? AND session_key IS NULL""",
                (donor["session_key"], donor["chat_id"], donor["chat_type"], donor["thread_id"],
                 donor["user_id"], donor["origin_json"], donor["display_name"], donor_id, orphan_id),
            )
            # Retire under a reason recovery does NOT treat as resumable — 'agent_close' /
            # 'ws_orphan_reap' would keep it in the running and the orphan could lose the chat again.
            conn.execute(
                "UPDATE sessions SET ended_at = COALESCE(ended_at, ?), "
                "end_reason = 'superseded_by_repair' WHERE id = ?",
                (time.time(), donor_id))
            return True
        return self._execute_write(_do)



    def rekey_profile_state(self, old_name: str, new_name: str) -> dict[str, int]:
        """Atomically rewrite exact profile identity in this state database."""
        old, new = (old_name or "").strip(), (new_name or "").strip()
        counts: dict[str, int] = {}
        if not old or not new or old == new:
            return counts
        old_ns, new_ns = f"agent:{old}:", f"agent:{new}:"
        ns_len = len(old_ns)

        def _do(conn):
            existing = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            topic_columns = _optional_table_columns(conn)
            collision = conn.execute(
                "SELECT old.scope, ? || substr(old.session_key, ?) "
                "FROM gateway_routing AS old JOIN gateway_routing AS target "
                "ON target.scope = old.scope "
                "AND target.session_key = ? || substr(old.session_key, ?) "
                "WHERE substr(old.session_key, 1, ?) = ? LIMIT 1",
                (new_ns, ns_len + 1, new_ns, ns_len + 1, ns_len, old_ns),
            ).fetchone()
            if collision is not None:
                raise ValueError(
                    f"profile routing collision in scope {collision[0]!r}: {collision[1]!r}")
            for table, columns in (
                ("telegram_dm_topic_mode", ("chat_id",)),
                ("telegram_dm_topic_bindings", ("chat_id", "thread_id")),
            ):
                if "profile_name" not in topic_columns.get(table, set()):
                    continue
                equality = " AND ".join(
                    f"target.{column} = old.{column}" for column in columns)
                collision = conn.execute(
                    f"SELECT 1 FROM {table} AS old JOIN {table} AS target "
                    f"ON target.profile_name = ? AND {equality} "
                    "WHERE old.profile_name = ? LIMIT 1", (new, old)).fetchone()
                if collision is not None:
                    raise ValueError(f"profile identity collision in {table}")

            counts["sessions_profile_name"] = conn.execute(
                "UPDATE sessions SET profile_name = ? WHERE profile_name = ?", (new, old)).rowcount
            counts["gateway_heartbeats_profile"] = conn.execute(
                "UPDATE gateway_heartbeats SET profile = ? WHERE profile = ?", (new, old)).rowcount
            counts["sessions_session_key"] = conn.execute(
                "UPDATE sessions SET session_key = ? || substr(session_key, ?) "
                "WHERE substr(session_key, 1, ?) = ?",
                (new_ns, ns_len + 1, ns_len, old_ns)).rowcount

            origin_count = 0
            for session_id, origin_json in conn.execute(
                    "SELECT id, origin_json FROM sessions WHERE origin_json IS NOT NULL").fetchall():
                try:
                    payload = json.loads(origin_json)
                except (ValueError, TypeError):
                    continue
                if isinstance(payload, dict) and payload.get("profile") == old:
                    payload["profile"] = new
                    conn.execute("UPDATE sessions SET origin_json = ? WHERE id = ?",
                                 (json.dumps(payload, ensure_ascii=False), session_id))
                    origin_count += 1
            counts["sessions_origin_json"] = origin_count

            if "delivery_obligations" in existing:
                if "adapter_profile" in topic_columns.get("delivery_obligations", set()):
                    counts["delivery_obligations_adapter_profile"] = conn.execute(
                        "UPDATE delivery_obligations SET adapter_profile = ? WHERE adapter_profile = ?",
                        (new, old)).rowcount
                counts["delivery_obligations_session_key"] = conn.execute(
                    "UPDATE delivery_obligations SET session_key = ? || substr(session_key, ?) "
                    "WHERE substr(session_key, 1, ?) = ?",
                    (new_ns, ns_len + 1, ns_len, old_ns)).rowcount
            for table in ("telegram_dm_topic_mode", "telegram_dm_topic_bindings"):
                if "profile_name" in topic_columns.get(table, set()):
                    counts[f"{table}_profile_name"] = conn.execute(
                        f"UPDATE {table} SET profile_name = ? WHERE profile_name = ?",
                        (new, old)).rowcount
            if "session_key" in topic_columns.get("telegram_dm_topic_bindings", set()):
                counts["telegram_dm_topic_bindings_session_key"] = conn.execute(
                    "UPDATE telegram_dm_topic_bindings "
                    "SET session_key = ? || substr(session_key, ?) "
                    "WHERE substr(session_key, 1, ?) = ?",
                    (new_ns, ns_len + 1, ns_len, old_ns)).rowcount

            routing = conn.execute(
                "SELECT rowid, session_key, entry_json FROM gateway_routing "
                "WHERE substr(session_key, 1, ?) = ?", (ns_len, old_ns)).fetchall()
            for rowid, session_key, entry_json in routing:
                new_session_key = new_ns + session_key[ns_len:]
                new_json = entry_json
                if entry_json:
                    try:
                        payload = json.loads(entry_json)
                    except (ValueError, TypeError):
                        payload = None
                    if isinstance(payload, dict):
                        if isinstance(payload.get("session_key"), str) and payload["session_key"].startswith(old_ns):
                            payload["session_key"] = new_ns + payload["session_key"][ns_len:]
                        origin = payload.get("origin")
                        if isinstance(origin, dict) and origin.get("profile") == old:
                            origin["profile"] = new
                        new_json = json.dumps(payload, ensure_ascii=False)
                conn.execute(
                    "UPDATE gateway_routing SET session_key = ?, entry_json = ? WHERE rowid = ?",
                    (new_session_key, new_json, rowid))
            counts["gateway_routing"] = len(routing)

        self._execute_write(_do)
        return counts

    def purge_profile_state(self, profile: str) -> dict[str, int]:
        """Delete exact profile identity from this state database (#111926, delete side).

        The mirror of :meth:`rekey_profile_state`: a rename must rekey a profile's identity, a
        delete must purge it. ``agent:<name>:*`` routing keys, ``gateway_heartbeats.profile``,
        ``delivery_obligations`` and the telegram topic tables are bookkeeping for a profile that
        no longer exists — left behind, every inbound event on a chat keyed to the deleted name
        enters the routing index, resolves a profile whose directory is gone, and logs
        ``Profile '<name>' does not exist`` on each event for the life of the store.

        What each store gets, and why:

        * Routing keys and heartbeat rows are hard-deleted — pure bookkeeping for a dead name.
        * ``delivery_obligations`` rows are **terminalized** (``state='abandoned'``), not deleted:
          a pending obligation is delivery state that should not vanish silently, and the ledger's
          own retention prunes abandoned rows. Delivered history is left as it was.
        * ``sessions`` rows are not deleted here: this helper settles identity, not history, and it
          does not decide what a delete leaves of a profile's conversation record — ``delete_profile``
          removes the profile's own home, ``state.db`` included, with the directory. Rows in a shared
          store keep whatever ownership they had; re-binding or archiving them belongs to the flow
          that recreates the name, not to this purge.

        Idempotent.
        """
        name = (profile or "").strip()
        counts: dict[str, int] = {}
        if not name:
            return counts
        ns, ns_len = f"agent:{name}:", len(f"agent:{name}:")

        def _do(conn):
            existing = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            topic_columns = _optional_table_columns(conn)
            if "gateway_routing" in existing:
                counts["gateway_routing"] = conn.execute(
                    "DELETE FROM gateway_routing WHERE substr(session_key, 1, ?) = ?",
                    (ns_len, ns)).rowcount
            if "gateway_heartbeats" in existing:
                counts["gateway_heartbeats"] = conn.execute(
                    "DELETE FROM gateway_heartbeats WHERE profile = ?", (name,)).rowcount
            if "delivery_obligations" in existing:
                # Terminalize, never hard-delete: a pending obligation is delivery state someone may
                # still care about, and the ledger's own retention prunes abandoned rows. Only
                # non-terminal rows are touched — delivered history is left exactly as it was.
                # A ledger created before ``adapter_profile`` existed matches on namespace alone.
                by_profile = ("adapter_profile = ? OR "
                              if "adapter_profile" in topic_columns.get("delivery_obligations", set())
                              else "")
                params = (time.time(), name, ns_len, ns) if by_profile else (time.time(), ns_len, ns)
                counts["delivery_obligations"] = conn.execute(
                    "UPDATE delivery_obligations SET state='abandoned', updated_at=? "
                    f"WHERE ({by_profile}substr(session_key, 1, ?) = ?) "
                    "AND state NOT IN ('delivered', 'abandoned')", params).rowcount
            if "profile_name" in topic_columns.get("telegram_dm_topic_mode", set()):
                counts["telegram_dm_topic_mode"] = conn.execute(
                    "DELETE FROM telegram_dm_topic_mode WHERE profile_name = ?", (name,)).rowcount
            binding_columns = topic_columns.get("telegram_dm_topic_bindings", set())
            if "session_key" in binding_columns:
                # A rename rewrites a binding's session_key namespace as well as its profile_name
                # (:meth:`rekey_profile_state`), so matching on one alone leaves the other behind.
                # Legacy v1/v2 bindings have no profile_name but keep the ``agent:<name>:`` namespace
                # in session_key, so the namespace match alone is the exact cleanup there.
                by_profile = "profile_name = ? OR " if "profile_name" in binding_columns else ""
                params = (name, ns_len, ns) if by_profile else (ns_len, ns)
                counts["telegram_dm_topic_bindings"] = conn.execute(
                    "DELETE FROM telegram_dm_topic_bindings "
                    f"WHERE {by_profile}substr(session_key, 1, ?) = ?", params).rowcount

        self._execute_write(_do)
        return counts

    @staticmethod
    def session_runtime(session_meta: Optional[dict[str, Any]]) -> dict[str, Any]:
        """Read the persisted runtime route off a session row dict (``model_config`` as
        JSON string or parsed dict). Precedence: nested ``gateway_runtime`` (gateway sync /
        CLI ``/model``), then top-level ``provider``/``base_url``/``api_mode`` (TUI), with
        ``billing_provider`` filling a missing provider so sessions that never ran ``/model``
        still restore the provider that served them. Empty dict on parse failure — resume uses ambient config."""
        from hermes_state import _BARE_BILLING_PROVIDERS
        raw = (session_meta or {}).get("model_config")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (ValueError, TypeError):
                raw = {}
        if not isinstance(raw, dict):
            raw = {}
        runtime = raw.get("runtime") or raw.get("gateway_runtime")
        # Filter None: the persist path writes or-None to trigger deletion in the top-level
        # merge, but gateway_runtime is replaced whole (not deep-merged), so None survives here.
        if isinstance(runtime, dict) and runtime.get("provider"):
            return {k: v for k, v in runtime.items() if v is not None}
        top_level = {key: raw.get(key) for key in ("provider", "base_url", "api_mode") if raw.get(key)}
        # billing_provider is COALESCE-written on the first accounted API call — the only durable
        # record for sessions that never ran /model. Bare buckets ("auto"/"custom") are not
        # routable identities; filter them so resume falls back to the ambient default.
        billing_provider = str((session_meta or {}).get("billing_provider") or "").strip()
        if billing_provider and billing_provider.lower() not in _BARE_BILLING_PROVIDERS:
            top_level.setdefault("provider", billing_provider)
        if top_level:
            return top_level
        if not isinstance(runtime, dict):
            return {}
        return {k: v for k, v in runtime.items() if v is not None}

    def register_backend_heartbeat(
        self, *, backend_id: str, pid: int, started_at: float, last_heartbeat: Optional[float] = None,
        profile: str = "", host: str = "") -> None:
        """Upsert this backend's liveness row. ``backend_id`` MUST be stable for the process
        lifetime (e.g. ``f"{profile}@{host}:{pid}"``) so a respawn cannot inherit a dead
        predecessor's heartbeat; ``started_at`` is when THIS process started, so a backend
        whose previous run died is not mistaken for a freshly-spawned sibling."""
        if not backend_id:
            return
        ts = time.time() if last_heartbeat is None else float(last_heartbeat)
        self._write_sql(
            "INSERT INTO gateway_heartbeats (backend_id, pid, started_at, last_heartbeat, profile, host)"
            " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(backend_id) DO UPDATE SET pid = excluded.pid,"
            " started_at = excluded.started_at, last_heartbeat = excluded.last_heartbeat,"
            " profile = excluded.profile, host = excluded.host",
            (str(backend_id), int(pid), float(started_at), ts, str(profile), str(host)))

    def clear_backend_heartbeat(self, backend_id: str) -> bool:
        """Remove this backend's heartbeat row (from ``atexit``); True if removed. A crashed
        backend's row is reclaimed later by ``prune_stale_heartbeats``."""
        if not backend_id:
            return False
        return self._write_rowcount(
            "DELETE FROM gateway_heartbeats WHERE backend_id = ?", (str(backend_id),)) > 0

    def prune_stale_heartbeats(self, *, max_age_seconds: float) -> list[str]:
        """Drop heartbeat rows older than the staleness window; return removed backend ids.
        Safe from any process — only stale rows are touched."""
        if max_age_seconds <= 0:
            return []
        cutoff = time.time() - max_age_seconds
        def _do(conn):
            cur = conn.execute(
                "DELETE FROM gateway_heartbeats WHERE last_heartbeat < ? RETURNING backend_id",
                (cutoff,))
            return [str(r[0]) for r in cur.fetchall()]
        return list(self._execute_write(_do) or [])

    def list_backend_heartbeats(self) -> list[dict[str, Any]]:
        """Snapshot of every backend heartbeat (diagnostics/tests); fields mirror the table."""
        rows = self._read_all(
            "SELECT backend_id, pid, started_at, last_heartbeat, profile, host FROM gateway_heartbeats"
            " ORDER BY last_heartbeat DESC")
        return [dict(r) for r in rows]
