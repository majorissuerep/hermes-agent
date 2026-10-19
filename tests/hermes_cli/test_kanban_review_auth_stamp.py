"""A successful review handoff supersedes old auth text, never a newer failure."""
import secrets

import pytest


@pytest.mark.parametrize("newer_failure", [False, True])
def test_review_handoff_uses_latest_run_without_erasing_failure_history(tmp_path, monkeypatch, newer_failure):
    from hermes_cli import kanban_db as kb, kanban_db_connect as kbc, kanban_db_dispatch as kbd
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_security import vault

    home = tmp_path / "home"
    password = secrets.token_urlsafe(24)
    vault.init_vault(home, password)
    vault.unlock(home, password)
    monkeypatch.setenv("HERMES_HOME", str(home))
    token = set_hermes_home_override(home)
    try:
        kb.init_db()
        with kbc.connect() as conn:
            tid = kb.create_task(conn, title="review after quota recovery", assignee="worker")
            claimed = kb.claim_task(conn, tid)
            conn.execute("UPDATE task_runs SET outcome='rate_limited', status='rate_limited', ended_at=99 WHERE id=?",
                         (claimed.current_run_id,))
            conn.execute("UPDATE tasks SET status='ready', current_run_id=NULL, claim_lock=NULL, claim_expires=NULL, "
                         "last_failure_error='rate-limited quota wall', consecutive_failures=2 WHERE id=?", (tid,))
            conn.commit()
            claimed = kb.claim_task(conn, tid)
            assert kb.request_review(conn, tid, expected_run_id=claimed.current_run_id)
            conn.execute("UPDATE task_runs SET ended_at=100 WHERE id=?", (claimed.current_run_id,))
            if newer_failure:
                conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))
                conn.commit()
                failed = kb.claim_task(conn, tid)
                conn.execute("UPDATE task_runs SET outcome='blocked', status='blocked', ended_at=100 WHERE id=?",
                             (failed.current_run_id,))
                conn.execute("UPDATE tasks SET last_failure_error='401 auth failed' WHERE id=?", (tid,))
            conn.commit()
            expected = "blocker_auth" if newer_failure else None
            assert kbd.check_respawn_guard(conn, tid, lane="review") == expected
            task = kb.get_task(conn, tid)
            assert task.consecutive_failures == 2
            assert task.last_failure_error == ("401 auth failed" if newer_failure else "rate-limited quota wall")
    finally:
        reset_hermes_home_override(token)
        vault.lock_now(home)
