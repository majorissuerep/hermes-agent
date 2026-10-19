"""Native retry and workspace guards read encrypted state without weakening their fences."""

import os
from pathlib import Path

import psutil
import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_connect import connect_closing


@pytest.fixture
def sealed_board(tmp_path, monkeypatch):
    from tests.e2e.vault_home import seal_fixture_home

    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("hermes_cli.profiles.resolve_profile_env", lambda name: str(home))
    seal_fixture_home(home)
    with connect_closing() as conn:
        task_id = kb.create_task(conn, title="encrypted work", assignee="default",
                                 workspace_kind="dir", workspace_path=str(tmp_path))
        task = kb.claim_task(conn, task_id, ttl_seconds=600)
    path = kb.kanban_db_path()
    assert not path.read_bytes().startswith(b"SQLite format 3\x00")
    for name, value in {"HERMES_KANBAN_TASK": task.id, "HERMES_KANBAN_RUN_ID": str(task.current_run_id),
                        "HERMES_KANBAN_CLAIM_LOCK": task.claim_lock, "HERMES_KANBAN_DB": str(path)}.items():
        monkeypatch.setenv(name, value)
    return home, task


@pytest.mark.parametrize("reader", ["carrier", "bound"])
def test_encrypted_worker_retry_requires_the_same_live_lease(sealed_board, reader):
    from agent.kanban_turn_recovery import worker_claim_is_live
    from hermes_cli.kanban_worker_client import _claim_context
    from hermes_cli.kanban_worker_turns import _bound_claim_is_live

    _, task = sealed_board
    _, context = _claim_context()
    check = worker_claim_is_live if reader == "carrier" else lambda: _bound_claim_is_live(context)
    assert check() is True
    with connect_closing() as conn, kb.write_txn(conn):
        conn.execute("UPDATE tasks SET claim_expires=1 WHERE id=?", (task.id,))
        conn.execute("UPDATE task_runs SET claim_expires=1 WHERE id=?", (task.current_run_id,))
    assert check() is False


def test_encrypted_other_board_retains_shared_workspace_until_released(sealed_board, tmp_path):
    from hermes_cli.kanban_db_workspace import _other_board_uses_path, _path_key

    home, task = sealed_board
    path = home / "other-board.db"
    shared = _path_key(tmp_path / "shared")
    with connect_closing(path) as conn:
        other_id = kb.create_task(conn, title="still using workspace", workspace_kind="dir",
                                  workspace_path=shared)
    assert _other_board_uses_path(path, task.id, shared) is True
    with connect_closing(path) as conn, kb.write_txn(conn):
        conn.execute("UPDATE tasks SET status='archived' WHERE id=?", (other_id,))
    assert _other_board_uses_path(path, task.id, shared) is False


def test_encrypted_historical_owner_receipt_preserves_reclaim_fence(sealed_board):
    from hermes_cli.kanban_owner_recovery import owner_reclaim_paused
    from hermes_security import sqlite

    home, task = sealed_board
    path = home / "legacy-owner.db"
    conn = sqlite.connect(path)
    try:
        conn.execute("CREATE TABLE session_admissions (target_session_id TEXT, request_id TEXT, status TEXT, outcome TEXT)")
        conn.execute("INSERT INTO session_admissions VALUES ('session', 'request', 'terminal', 'completed')")
        conn.commit()
        with connect_closing() as board, kb.write_txn(board):
            kb._append_event(board, task.id, "owner_admitted", {
                "db": str(path), "session_id": "session", "request_id": "request",
                "pid": os.getpid(), "birth": psutil.Process().create_time(),
            }, run_id=task.current_run_id)
        with connect_closing() as board:
            assert owner_reclaim_paused(board, task.id) is False
        conn.execute("UPDATE session_admissions SET status='queued', outcome=NULL")
        conn.commit()
        with connect_closing() as board:
            assert owner_reclaim_paused(board, task.id) is True
    finally:
        conn.close()
