import pytest

from pathlib import Path
from hermes_cli import kanban as kc
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_notify as kbn

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Allow the kanban notifier path-validator to upload artifacts the
    # tests write under ``tmp_path``. Without this, every artifact-delivery
    # test silently drops files because ``tmp_path`` isn't inside the
    # default ``MEDIA_DELIVERY_SAFE_ROOTS`` cache dirs.
    monkeypatch.setenv("HERMES_MEDIA_ALLOW_DIRS", str(tmp_path))
    kb.init_db()
    return home

def test_notify_sub_delivery_mode_persists_and_last_write_wins(kanban_home):
    """delivery_mode persists; an explicit re-subscribe is last-write-wins, a
    ``None`` re-subscribe leaves the existing mode untouched, an unknown value
    is ignored, and none of this clobbers the notifier_profile owner."""
    import hermes_cli.kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_notify as kbn

    conn = kbc.connect()
    try:
        tid = kb.create_task(conn, title="mode sub task", assignee="worker1")
        # Fresh sub without a mode -> defaults to "notify".
        kbn.add_notify_sub(
            conn, task_id=tid, platform="telegram", chat_id="chat1",
            notifier_profile="owner-a",
        )
        subs = kbn.list_notify_subs(conn, tid)
        assert len(subs) == 1
        assert subs[0]["delivery_mode"] == "notify"
        assert subs[0]["notifier_profile"] == "owner-a"

        # Explicit re-subscribe changes the mode (last-write-wins) and must NOT
        # overwrite the existing owner (owner self-heals only when unset).
        kbn.add_notify_sub(
            conn, task_id=tid, platform="telegram", chat_id="chat1",
            notifier_profile="owner-b", delivery_mode="wake",
        )
        subs = kbn.list_notify_subs(conn, tid)
        assert len(subs) == 1
        assert subs[0]["delivery_mode"] == "wake"
        assert subs[0]["notifier_profile"] == "owner-a"

        # A None re-subscribe leaves the existing mode untouched.
        kbn.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat1")
        subs = kbn.list_notify_subs(conn, tid)
        assert subs[0]["delivery_mode"] == "wake"

        # An unknown mode is ignored (treated like None: no clobber).
        kbn.add_notify_sub(
            conn, task_id=tid, platform="telegram", chat_id="chat1",
            delivery_mode="bogus",
        )
        subs = kbn.list_notify_subs(conn, tid)
        assert subs[0]["delivery_mode"] == "wake"
    finally:
        conn.close()

def test_cli_can_remove_historical_remote_subscription(kanban_home):
    """Retiring the transport preserves old records until explicit local cleanup."""
    import argparse

    with kbc.connect_closing() as conn:
        task = kb.create_task(conn, title="old route", assignee="worker")
        kbn.add_notify_sub(conn, task_id=task, platform="discord", chat_id="thread",
                           thread_id="thread", delivery_metadata={"guild_id": "old-guild"})
        kbn.add_notify_sub(conn, task_id=task, platform="tui", chat_id="local-owner")

    parser = argparse.ArgumentParser()
    kc.build_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args([
        "kanban", "notify-unsubscribe", task, "--platform", "discord", "--chat-id", "thread",
        "--thread-id", "thread",
    ])
    assert kc.kanban_command(args) == 0
    with kbc.connect_closing() as conn:
        subscription, = kbn.list_notify_subs(conn, task)
        assert kb.get_task(conn, task) is not None
    assert (subscription["platform"], subscription["chat_id"]) == ("tui", "local-owner")


def test_notify_sub_chat_type_persists_and_last_write_wins(kanban_home):
    """chat_type persists, defaults to 'dm', an explicit re-subscribe is
    last-write-wins, and a None re-subscribe leaves it untouched. The
    active-wake path replays this field so the woken turn keys to the
    operator's real channel."""
    import hermes_cli.kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_notify as kbn

    conn = kbc.connect()
    try:
        tid = kb.create_task(conn, title="chat_type sub", assignee="worker1")
        # Fresh sub without chat_type -> defaults to "dm".
        kbn.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat1")
        subs = kbn.list_notify_subs(conn, tid)
        assert subs[0]["chat_type"] == "dm"

        # Explicit re-subscribe corrects the recorded chat_type (last-write-wins).
        kbn.add_notify_sub(
            conn, task_id=tid, platform="telegram", chat_id="chat1",
            chat_type="group",
        )
        subs = kbn.list_notify_subs(conn, tid)
        assert subs[0]["chat_type"] == "group"

        # A None re-subscribe (here changing only the mode) must NOT clobber
        # the recorded chat_type.
        kbn.add_notify_sub(
            conn, task_id=tid, platform="telegram", chat_id="chat1",
            delivery_mode="wake",
        )
        subs = kbn.list_notify_subs(conn, tid)
        assert subs[0]["chat_type"] == "group"
        assert subs[0]["delivery_mode"] == "wake"
    finally:
        conn.close()

def test_notify_sub_user_id_backfills_legacy_senderless_rows(kanban_home):
    import hermes_cli.kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_notify as kbn

    conn = kbc.connect()
    try:
        tid = kb.create_task(conn, title="legacy sub", assignee="worker1")
        kbn.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat1")
        assert kbn.list_notify_subs(conn, tid)[0]["user_id"] is None

        kbn.add_notify_sub(
            conn, task_id=tid, platform="telegram", chat_id="chat1", user_id="640466638",
        )
        subs = kbn.list_notify_subs(conn, tid)
    finally:
        conn.close()

    assert subs[0]["user_id"] == "640466638"

def test_notify_sub_user_id_alt_persists_and_backfills_legacy_rows(kanban_home):
    """user_id_alt is persisted with the notify subscription routing tuple and
    can backfill a pre-existing row created before the alt id was known."""
    import hermes_cli.kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_notify as kbn

    conn = kbc.connect()
    try:
        tid = kb.create_task(conn, title="alt sub", assignee="worker1")
        kbn.add_notify_sub(
            conn, task_id=tid, platform="telegram", chat_id="chat1",
            user_id="open-id",
        )
        subs = kbn.list_notify_subs(conn, tid)
        assert subs[0]["user_id"] == "open-id"
        assert subs[0]["user_id_alt"] is None

        kbn.add_notify_sub(
            conn, task_id=tid, platform="telegram", chat_id="chat1",
            user_id="open-id", user_id_alt="union-id",
        )
        subs = kbn.list_notify_subs(conn, tid)
    finally:
        conn.close()

    assert subs[0]["user_id"] == "open-id"
    assert subs[0]["user_id_alt"] == "union-id"












# ---------------------------------------------------------------------------
# Migration backfill: pre-delivery_mode gateway subscriptions keep active wake.
#
# Before the delivery_mode column existed, the notifier woke the originating
# session unconditionally whenever the task carried a session_id — so every
# pre-existing gateway subscription had de facto active wake. The column's
# 'notify' DEFAULT alone would silently disable that on upgrade. The migration
# backfills first-add rows: gateway platforms -> 'notify+wake', tui -> 'notify'.
# ---------------------------------------------------------------------------

def test_migration_backfills_legacy_gateway_subs_to_notify_wake(kanban_home):
    from hermes_cli.kanban_db_connect import _migrate_add_optional_columns

    with kbc.connect() as conn:
        task_id = kb.create_task(conn, title="legacy sub upgrade")
        # Simulate a pre-delivery_mode database: drop the column entirely,
        # then insert legacy-shaped rows (one gateway, one tui).
        conn.execute("ALTER TABLE kanban_notify_subs DROP COLUMN delivery_mode")
        conn.execute(
            "INSERT INTO kanban_notify_subs "
            "(task_id, platform, chat_id, thread_id, created_at) "
            "VALUES (?, 'telegram', 'legacy-chat', '', 1)",
            (task_id,),
        )
        conn.execute(
            "INSERT INTO kanban_notify_subs "
            "(task_id, platform, chat_id, thread_id, created_at) "
            "VALUES (?, 'tui', 'tui-session-1', '', 1)",
            (task_id,),
        )
        # Re-run the idempotent migration: first-add of delivery_mode must
        # backfill gateway rows to notify+wake and leave tui rows on notify.
        _migrate_add_optional_columns(conn)
        rows = {
            r["platform"]: r["delivery_mode"]
            for r in conn.execute(
                "SELECT platform, delivery_mode FROM kanban_notify_subs "
                "WHERE task_id = ?",
                (task_id,),
            )
        }
    assert rows["telegram"] == "notify+wake", (
        "Legacy gateway subscription lost active wake across the upgrade"
    )
    assert rows["tui"] == "notify"

def test_migration_backfill_runs_only_on_first_add(kanban_home):
    from hermes_cli.kanban_db_connect import _migrate_add_optional_columns

    with kbc.connect() as conn:
        task_id = kb.create_task(conn, title="explicit downgrade survives")
        kbn.add_notify_sub(
            conn,
            task_id=task_id,
            platform="telegram",
            chat_id="chat-x",
            delivery_mode="notify",
        )
        # Column already exists -> re-running the migration must NOT touch
        # the user's explicit 'notify' choice.
        _migrate_add_optional_columns(conn)
        row = conn.execute(
            "SELECT delivery_mode FROM kanban_notify_subs WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    assert row["delivery_mode"] == "notify"

# ---------------------------------------------------------------------------
# Issue #73030: _inherit_notify_subs (link_tasks / decompose path) must copy
# EVERY routing column — chat_type, user_id_alt, delivery_mode, and
# delivery_metadata. Before the fix it copied only platform/chat/thread/user/
# profile, so a DM-originated child completion fell back to chat_type='group'
# and woke a fresh group-scoped session instead of the originating DM, and
# Telegram DM-topic subs lost their persisted reply-fallback metadata.
# ---------------------------------------------------------------------------

def _add_full_parent_sub(kb, conn, parent):
    kbn.add_notify_sub(
        conn, task_id=parent, platform="telegram", chat_id="chat1",
        thread_id="topic1", user_id="user1", user_id_alt="alt-1",
        chat_type="dm", notifier_profile="default",
        delivery_mode="notify+wake",
        delivery_metadata={"reply_fallback": "general", "topic_name": "ops"},
    )

def _assert_full_inherited_sub(subs):
    assert len(subs) == 1
    s = subs[0]
    assert s["platform"] == "telegram"
    assert s["chat_id"] == "chat1"
    assert s["thread_id"] == "topic1"
    assert s["user_id"] == "user1"
    assert s["user_id_alt"] == "alt-1", "user_id_alt dropped during inheritance"
    assert s["chat_type"] == "dm", (
        "chat_type dropped during inheritance — wake would key to a "
        "group-scoped session instead of the originating DM (issue #73030)"
    )
    assert s["delivery_mode"] == "notify+wake"
    md = s["delivery_metadata"]
    assert md and md.get("reply_fallback") == "general", (
        "delivery_metadata dropped during inheritance (issue #73030)"
    )

def test_link_tasks_inherits_all_routing_columns(kanban_home):
    import hermes_cli.kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_notify as kbn

    conn = kbc.connect()
    try:
        parent = kb.create_task(conn, title="root", assignee=None)
        _add_full_parent_sub(kb, conn, parent)
        # Pre-existing child, linked after the fact — exercises
        # _inherit_notify_subs directly (not the create_task parents path).
        child = kb.create_task(conn, title="existing child", assignee="w1")
        kb.link_tasks(conn, parent, child)
        subs = kbn.list_notify_subs(conn, child)
    finally:
        conn.close()
    _assert_full_inherited_sub(subs)

def test_create_with_parents_inherits_delivery_metadata(kanban_home):
    import hermes_cli.kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_notify as kbn

    conn = kbc.connect()
    try:
        parent = kb.create_task(conn, title="root", assignee=None)
        _add_full_parent_sub(kb, conn, parent)
        child = kb.create_task(
            conn, title="graph child", assignee="w1", parents=[parent],
        )
        subs = kbn.list_notify_subs(conn, child)
    finally:
        conn.close()
    _assert_full_inherited_sub(subs)

# ---------------------------------------------------------------------------
# Stale done-subscription GC (purge_stale_done_notify_subs)
# ---------------------------------------------------------------------------

def _make_done_task_with_sub(kb, conn, *, title, chat_id):
    tid = kb.create_task(conn, title=title, assignee="worker1")
    kbn.add_notify_sub(
        conn, task_id=tid, platform="telegram", chat_id=chat_id,
        notifier_profile="default",
    )
    assert kb.complete_task(conn, tid, summary="done")
    return tid

def _backdate_task(kb, conn, tid, *, days):
    """Push a task's entire event history + completion into the past."""
    past = int(__import__("time").time()) - days * 86400
    with kb.write_txn(conn):
        conn.execute(
            "UPDATE task_events SET created_at = ? WHERE task_id = ?",
            (past, tid),
        )
        conn.execute(
            "UPDATE tasks SET completed_at = ?, created_at = ? WHERE id = ?",
            (past, past, tid),
        )

def test_gc_purges_stale_done_sub_keeps_fresh_one(kanban_home):
    import hermes_cli.kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_notify as kbn

    conn = kbc.connect()
    try:
        stale = _make_done_task_with_sub(kb, conn, title="old done", chat_id="c-stale")
        fresh = _make_done_task_with_sub(kb, conn, title="new done", chat_id="c-fresh")
        _backdate_task(kb, conn, stale, days=45)

        purged = kbn.purge_stale_done_notify_subs(conn, max_age_days=30)

        assert purged == 1
        assert kbn.list_notify_subs(conn, stale) == []
        # A done task inside the retention window keeps its subscription —
        # it may still be reopened for review corrections.
        assert len(kbn.list_notify_subs(conn, fresh)) == 1
    finally:
        conn.close()

def test_gc_honors_explicit_retention_days(kanban_home):
    import hermes_cli.kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_notify as kbn

    conn = kbc.connect()
    try:
        tid = _make_done_task_with_sub(kb, conn, title="ten days old", chat_id="c-10d")
        _backdate_task(kb, conn, tid, days=10)

        # A 10-day-old done task is fresh under an explicit 30-day policy.
        assert kbn.purge_stale_done_notify_subs(conn, max_age_days=30) == 0
        assert len(kbn.list_notify_subs(conn, tid)) == 1

        # A tighter user-configured retention purges the same row.
        assert kbn.purge_stale_done_notify_subs(conn, max_age_days=7) == 1
        assert kbn.list_notify_subs(conn, tid) == []

        # Zero (and below) disables the sweep entirely.
        tid2 = _make_done_task_with_sub(kb, conn, title="ancient", chat_id="c-anc")
        _backdate_task(kb, conn, tid2, days=3650)
        assert kbn.purge_stale_done_notify_subs(conn, max_age_days=0) == 0
        assert len(kbn.list_notify_subs(conn, tid2)) == 1
    finally:
        conn.close()

def test_gc_spares_reopened_task_even_when_old(kanban_home):
    import hermes_cli.kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_notify as kbn

    conn = kbc.connect()
    try:
        tid = _make_done_task_with_sub(kb, conn, title="reopened", chat_id="c-reopen")
        _backdate_task(kb, conn, tid, days=90)
        # Reopen: the task leaves ``done``, so even with an ancient event
        # history the GC must not touch its subscription.
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status = 'ready' WHERE id = ?", (tid,))
            kb._append_event(conn, tid, "status", {"status": "ready"})
        # Backdate the reopen event too — status alone must protect it.
        _backdate_task(kb, conn, tid, days=90)

        assert kbn.purge_stale_done_notify_subs(conn, max_age_days=30) == 0
        assert len(kbn.list_notify_subs(conn, tid)) == 1
    finally:
        conn.close()

def _set_task_status(kb, conn, tid, status):
    """Force a task into ``status`` with a matching status event."""
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, tid))
        kb._append_event(conn, tid, "status", {"status": status})

def test_gc_purges_blocked_task_that_never_done(kanban_home):
    import hermes_cli.kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_notify as kbn

    conn = kbc.connect()
    try:
        tid = kb.create_task(conn, title="stuck blocked", assignee="worker1")
        kbn.add_notify_sub(
            conn, task_id=tid, platform="telegram", chat_id="c-blocked",
            notifier_profile="default",
        )
        _set_task_status(kb, conn, tid, "blocked")
        _backdate_task(kb, conn, tid, days=45)

        purged = kbn.purge_stale_done_notify_subs(conn, max_age_days=30)

        assert purged == 1
        assert kbn.list_notify_subs(conn, tid) == []
    finally:
        conn.close()
