"""A terminal task binds only its exact live claim and records the attempt result locally."""
import json
from pathlib import Path
import pytest


def test_local_worker_claim_is_exact_and_cannot_bind_twice(tmp_path, monkeypatch):
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing
    from hermes_cli import kanban_worker_client as worker
    from hermes_cli.kanban_worker_turns import _record_worker_result
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr('hermes_cli.profiles.resolve_profile_env', lambda name: str(home))
    kb.init_db()
    with connect_closing() as conn:
        task_id = kb.create_task(conn, title='Local terminal task', assignee='default',
                                 workspace_kind='dir', workspace_path=str(tmp_path))
        task = kb.claim_task(conn, task_id, ttl_seconds=600)
    assert task is not None
    for name, value in {'HERMES_KANBAN_TASK': task.id, 'HERMES_KANBAN_RUN_ID': str(task.current_run_id),
                        'HERMES_KANBAN_CLAIM_LOCK': task.claim_lock, 'HERMES_KANBAN_DB': str(kb.kanban_db_path())}.items():
        monkeypatch.setenv(name, value)
    _, context = worker._claim_context()
    with pytest.raises(RuntimeError, match='already_running'):
        worker._claim_context()
    _record_worker_result(context, 0, 'completed locally')
    with connect_closing() as conn:
        row = conn.execute("SELECT payload FROM task_events WHERE task_id=? AND kind='worker_result'", (task.id,)).fetchone()
    assert json.loads(row[0])['last_output'] == 'completed locally'
    monkeypatch.setenv('HERMES_KANBAN_CLAIM_LOCK', 'not-this-claim')
    with pytest.raises(RuntimeError, match='stale_kanban_claim'):
        worker._claim_context()
