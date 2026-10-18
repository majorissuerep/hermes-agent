"""The generalized change watcher (#73618): cheap on-disk signatures →
``pet.changed`` / ``cron.changed`` / ``sessions.changed`` global broadcasts.

Behavior contracts, exercised against a real temp HERMES_HOME (no mocks on the
filesystem path): first sighting seeds silently, a moved signature broadcasts
once, the sessions floor coalesces a write burst but keeps its trailing edge,
and the pet signature only moves for a *renderable* pet.
"""

import os
import sqlite3
import time

import pytest

from tui_runtime import server


@pytest.fixture()
def watcher_home(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("display: {}\n")
    (tmp_path / "cron").mkdir()

    monkeypatch.setattr(server, "_hermes_home", str(tmp_path))
    monkeypatch.setattr(server, "_cfg_cache", None)
    monkeypatch.setattr(server, "_change_sigs", {})
    monkeypatch.setattr(server, "_change_checked_at", {})
    monkeypatch.setattr(server, "_change_broadcast_at", {})
    monkeypatch.setattr(server, "_sessions_db_sig_cache", {})

    events = []
    monkeypatch.setattr(
        server, "_broadcast_global_event", lambda ev, payload=None: events.append((ev, payload))
    )
    return tmp_path, events


def _write_session_change(db_path, title):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions "
        "(id TEXT PRIMARY KEY, title TEXT, last_activity_at REAL, message_count INTEGER)"
    )
    conn.execute(
        "INSERT INTO sessions(id, title, last_activity_at, message_count) VALUES ('s1', ?, 1, 1) "
        "ON CONFLICT(id) DO UPDATE SET title = excluded.title",
        (title,),
    )
    conn.commit()
    conn.close()


def test_first_sighting_seeds_without_broadcasting(watcher_home):
    home, events = watcher_home
    (home / "cron" / "jobs.json").write_text("[]")
    (home / "state.db").write_text("x")

    server._broadcast_watched_changes(now=0.0)

    assert events == []


def test_cron_jobs_file_move_broadcasts_cron_changed(watcher_home):
    home, events = watcher_home
    server._broadcast_watched_changes(now=0.0)

    (home / "cron" / "jobs.json").write_text("[]")
    server._broadcast_watched_changes(now=10.0)

    assert ("cron.changed", {}) in events


def test_state_db_move_broadcasts_sessions_changed(watcher_home):
    home, events = watcher_home
    server._broadcast_watched_changes(now=0.0)

    _write_session_change(home / "state.db", "created")
    server._broadcast_watched_changes(now=10.0)

    assert ("sessions.changed", {}) in events


def _seed_store(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, started_at REAL, "
        "message_count INTEGER, last_activity_at REAL, last_activity_description TEXT);"
        "CREATE TABLE gateway_heartbeats (backend_id TEXT PRIMARY KEY, last_heartbeat REAL);"
        "INSERT INTO sessions VALUES ('s1', 'hello', 1, 3, 1, NULL);"
        "INSERT INTO gateway_heartbeats VALUES ('backend-1', 1);"
    )
    conn.commit()
    conn.close()


def _write(db_path, sql, *params):
    conn = sqlite3.connect(db_path)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def test_activity_heartbeats_do_not_broadcast_sessions_changed(watcher_home):
    """#98005: the gateway heartbeat and the session activity stamp rewrite state.db every
    minute with no session change. Each used to fire sessions.changed, so the Desktop
    Sessions panel refreshed on its own every heartbeat window."""
    home, events = watcher_home
    db = home / "state.db"
    _seed_store(db)
    server._broadcast_watched_changes(now=0.0)

    for tick in range(1, 4):
        time.sleep(0.02)
        _write(db, "UPDATE gateway_heartbeats SET last_heartbeat = ?", tick * 60.0)
        _write(db, "UPDATE sessions SET last_activity_at = ?, last_activity_description = ? "
                   "WHERE id = 's1'", tick * 60.0, f"tool {tick}")
        server._broadcast_watched_changes(now=tick * 10.0)

    assert ("sessions.changed", {}) not in events


@pytest.mark.parametrize("sql", [
    "INSERT INTO sessions VALUES ('s2', 'new', 2, 0, 2, NULL)",
    "UPDATE sessions SET title = 'renamed' WHERE id = 's1'",
    "UPDATE sessions SET message_count = 4 WHERE id = 's1'",
    "DELETE FROM sessions WHERE id = 's1'",
])
def test_session_row_changes_broadcast_sessions_changed(watcher_home, sql):
    home, events = watcher_home
    db = home / "state.db"
    _seed_store(db)
    server._broadcast_watched_changes(now=0.0)

    time.sleep(0.02)
    _write(db, sql)
    server._broadcast_watched_changes(now=10.0)

    assert ("sessions.changed", {}) in events


def test_unreadable_store_keeps_last_digest(watcher_home):
    """A locked/unreadable moment after a good read must not flip to the mtime signature:
    digest -> mtime -> digest would broadcast twice for nothing."""
    home, events = watcher_home
    db = home / "state.db"
    _seed_store(db)
    server._broadcast_watched_changes(now=0.0)

    db.write_text("not-sqlite")
    server._broadcast_watched_changes(now=10.0)

    assert ("sessions.changed", {}) not in events


def test_projects_db_move_broadcasts_projects_changed(watcher_home):
    """#53046 / #56757: the CLI and other windows write projects.db directly, in
    processes that never touch this gateway's transports. Without a watch, a
    `hermes projects create` (or a set_primary / folder edit from another
    window) leaves the Desktop's project tree stale until an unrelated refresh."""
    home, events = watcher_home
    server._broadcast_watched_changes(now=0.0)

    (home / "projects.db").write_text("x")
    server._broadcast_watched_changes(now=10.0)

    assert ("projects.changed", {}) in events


def test_served_profile_projects_db_move_broadcasts_projects_changed(watcher_home, monkeypatch):
    """A backend serving a sibling profile watches that profile's projects.db too —
    the sibling-home half of the sessions.changed contract (#53046)."""
    home, events = watcher_home
    coder_home = home / "profiles" / "coder"
    coder_home.mkdir(parents=True)
    monkeypatch.setattr(server, "_served_profile_homes", set())
    monkeypatch.setattr("hermes_cli.profiles.get_profile_dir", lambda name: home / "profiles" / name)
    assert server._profile_home("coder") == coder_home
    server._broadcast_watched_changes(now=0.0)

    (coder_home / "projects.db").write_text("x")
    server._broadcast_watched_changes(now=10.0)

    assert ("projects.changed", {}) in events


def test_projects_sig_does_not_track_state_db_writes(watcher_home):
    """A state.db move must not fire projects.changed — the two stores are
    independent and their consumers refetch different surfaces."""
    home, events = watcher_home
    server._broadcast_watched_changes(now=0.0)

    (home / "state.db").write_text("x")
    server._broadcast_watched_changes(now=10.0)

    assert ("projects.changed", {}) not in events


def test_served_profile_store_move_broadcasts_sessions_changed(watcher_home, monkeypatch):
    """A backend serving a sibling profile must see that profile's state.db
    move too — otherwise a routed profile's Bot Chat never refreshes (#99333)."""
    home, events = watcher_home
    bot_home = home / "profiles" / "bot"
    bot_home.mkdir(parents=True)
    monkeypatch.setattr(server, "_served_profile_homes", set())
    monkeypatch.setattr("hermes_cli.profiles.get_profile_dir", lambda name: home / "profiles" / name)
    assert server._profile_home("bot") == bot_home
    server._broadcast_watched_changes(now=0.0)

    _write_session_change(bot_home / "state.db", "created")
    server._broadcast_watched_changes(now=10.0)

    assert ("sessions.changed", {}) in events












def test_sessions_floor_coalesces_burst_but_keeps_trailing_edge(watcher_home):
    home, events = watcher_home
    server._broadcast_watched_changes(now=0.0)

    _write_session_change(home / "state.db", "first")
    server._broadcast_watched_changes(now=10.0)
    events.clear()

    # A second write lands inside the 2s floor: no broadcast yet…
    time.sleep(0.02)
    _write_session_change(home / "state.db", "second")
    server._broadcast_watched_changes(now=11.0)
    assert events == []

    # …but the change is not lost — it fires once the window opens.
    server._broadcast_watched_changes(now=13.0)
    assert ("sessions.changed", {}) in events


def test_pet_sig_stays_off_without_a_renderable_pet(watcher_home):
    home, events = watcher_home
    server._broadcast_watched_changes(now=0.0)

    # Config flips enabled but no pet exists on disk → signature stays ("off",).
    (home / "config.yaml").write_text("display:\n  pet:\n    enabled: true\n    slug: boba\n")
    server._cfg_cache = None
    server._broadcast_watched_changes(now=10.0)

    assert not [e for e in events if e[0] == "pet.changed"]


def test_renderable_pet_broadcasts_meta_payload(watcher_home, monkeypatch):
    home, events = watcher_home
    (home / "config.yaml").write_text("display:\n  pet:\n    enabled: true\n    slug: boba\n")
    server._cfg_cache = None
    server._broadcast_watched_changes(now=0.0)

    sheet = home / "sheet.png"
    sheet.write_text("png")

    class FakePet:
        slug = "boba"
        display_name = "Boba"
        exists = True
        spritesheet = sheet

    monkeypatch.setattr(server, "_pet_active_selection", lambda: (True, FakePet(), 0.33))
    server._broadcast_watched_changes(now=10.0)

    pet_events = [e for e in events if e[0] == "pet.changed"]
    assert pet_events
    payload = pet_events[0][1]
    assert payload["enabled"] is True
    assert payload["slug"] == "boba"
    assert payload["spritesheetRevision"]










def test_broken_probe_never_kills_the_pass(watcher_home, monkeypatch):
    home, events = watcher_home
    server._broadcast_watched_changes(now=0.0)

    monkeypatch.setitem(
        server._CHANGE_WATCHES,
        "cron.changed",
        (1.0, lambda: (_ for _ in ()).throw(RuntimeError("boom")), dict),
    )
    _write_session_change(home / "state.db", "created")
    server._broadcast_watched_changes(now=10.0)

    # The broken cron probe is skipped; sessions still broadcasts.
    assert ("sessions.changed", {}) in events
