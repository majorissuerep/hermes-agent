"""Completed scheduled attempts survive a jobs.json rollback, not just a process restart."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from datetime import UTC


def test_completed_occurrence_survives_job_store_rollback(tmp_path, monkeypatch):
    from datetime import timedelta
    from hermes_time import now
    from cron import jobs, scheduler
    from hermes_security import vault

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    vault.init_vault(tmp_path, "synthetic-occurrence-password")
    vault.unlock(tmp_path, "synthetic-occurrence-password")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    effect = tmp_path / "effects.txt"
    script = scripts / "effect.py"
    from hermes_security.io import write_text
    write_text(script, "from pathlib import Path\np=Path(" + repr(str(effect)) + ")\nwith p.open('a') as f: f.write('effect\\n')\nprint('done')\n", purpose="state")
    jobs.create_job(prompt="", schedule="every 4h", script="effect.py", no_agent=True)
    rows = jobs.load_jobs()
    rows[0]["next_run_at"] = (now() - timedelta(minutes=30)).isoformat()
    snapshot = json.loads(json.dumps(rows))
    jobs.save_jobs(rows)
    assert scheduler.tick(verbose=False, sync=True) == 1
    assert effect.read_text().splitlines() == ["effect"]
    jobs.save_jobs(snapshot)
    assert scheduler.tick(verbose=False, sync=True) == 0
    assert effect.read_text().splitlines() == ["effect"]
    snapshot[0]["next_run_at"] = (now() - timedelta(minutes=10)).isoformat()
    jobs.save_jobs(snapshot)
    assert scheduler.tick(verbose=False, sync=True) == 1
    assert effect.read_text().splitlines() == ["effect", "effect"]


def test_ledger_migration_and_completion_identity(tmp_path, monkeypatch):
    import sqlite3
    from cron import executions, jobs
    from cron.occurrences import completed_occurrence

    db = tmp_path / 'executions.db'
    monkeypatch.setattr(executions, 'EXECUTIONS_FILE', db)
    # Pre-migration schema, not initialized by the implementation under test.
    with sqlite3.connect(db) as conn:
        conn.execute('''CREATE TABLE executions (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL, source TEXT NOT NULL,
            process_id TEXT NOT NULL, pid INTEGER NOT NULL, process_started_at INTEGER,
            status TEXT NOT NULL, claimed_at TEXT NOT NULL,
            started_at TEXT, finished_at TEXT, error TEXT)''')
        conn.execute("INSERT INTO executions VALUES "
                     "('legacy','job','builtin','old',-1,NULL,'completed',"
                     "'2026-01-01T00:00:00Z',NULL,NULL,NULL)")
    slot = '2026-01-01T00:00:00+00:00'
    job = {'id': 'job'}
    assert not completed_occurrence(job, slot)
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT id, scheduled_instant FROM executions').fetchall() == [('legacy', None)]
    for status in ('failed', 'unknown', 'running', 'claimed'):
        row = executions.create_execution('job', source='control', scheduled_instant=slot)
        with sqlite3.connect(db) as conn:
            conn.execute('UPDATE executions SET status=? WHERE id=?', (status, row['id']))
        assert not completed_occurrence(job, slot)
    row = executions.create_execution('job', source='builtin', scheduled_instant=slot)
    executions.finish_execution(row['id'], success=True)
    executions.create_execution('job', source='manual')  # latest row is not the completed one
    assert completed_occurrence(job, '2026-01-01T08:00:00+0800')
    assert not completed_occurrence(job, '2026-01-01T00:00:01Z')
    assert not completed_occurrence({'id': 'other'}, slot)

    # A claimed scheduled fire captures its instant before schedule advancement.
    with jobs.use_cron_store(tmp_path / 'cron'):
        stored = jobs.create_job(prompt='test', schedule='every 4h')
        rows = jobs.load_jobs()
        rows[0]['next_run_at'] = slot
        jobs.save_jobs(rows)
        due = jobs.get_due_jobs()[0]
        assert due['_scheduled_instant'] == slot
        assert jobs.claim_job_for_fire(stored['id'])
        row = executions.create_execution(stored['id'], source='direct', scheduled_instant=due['_scheduled_instant'])
        executions.mark_execution_handoff_pending(row['id'])
        adopted = executions.adopt_claimed_execution(row['id'])
        assert adopted['scheduled_instant'] == slot

        # A runnable legacy wall-clock value cannot establish an exact UTC identity.
        from datetime import timedelta
        from hermes_time import now
        naive = jobs.create_job(prompt='legacy', schedule='every 4h')
        rows = jobs.load_jobs()
        for item in rows:
            if item['id'] == naive['id']:
                item['next_run_at'] = (now() - timedelta(minutes=10)).replace(tzinfo=None).isoformat()
        jobs.save_jobs(rows)
        due = next(item for item in jobs.get_due_jobs() if item['id'] == naive['id'])
        assert due['_scheduled_instant'] is None


def test_completion_before_occurrence_does_not_prove_slot_completed(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from cron import executions
    from cron.occurrences import completed_occurrence

    monkeypatch.setattr(executions, 'EXECUTIONS_FILE', tmp_path / 'executions.db')
    slot = '2026-01-05T00:00:00+00:00'
    monkeypatch.setattr(executions, '_hermes_now', lambda: datetime(2026, 1, 1, tzinfo=UTC))
    poisoned = executions.create_execution('job', source='control', scheduled_instant=slot)
    executions.finish_execution(poisoned['id'], success=True)

    assert not completed_occurrence({'id': 'job'}, slot)

    monkeypatch.setattr(executions, '_hermes_now', lambda: datetime(2026, 1, 5, tzinfo=UTC))
    legitimate = executions.create_execution('job', source='builtin', scheduled_instant=slot)
    executions.finish_execution(legitimate['id'], success=True)

    assert completed_occurrence({'id': 'job'}, slot)


def test_completed_occurrence_skip_names_job_slot_and_row(tmp_path, monkeypatch, caplog):
    """A due slot the dedup gate consumes (already completed, e.g. after a jobs.json rollback)
    leaves no run and no ledger row, so the skip itself must be logged with the job, the
    instant and the completed execution (#111414: a consumed slot left zero trace)."""
    import logging
    from datetime import timedelta

    from cron import executions, jobs
    from hermes_time import now

    monkeypatch.setattr(executions, 'EXECUTIONS_FILE', tmp_path / 'executions.db')
    slot = (now() - timedelta(minutes=2)).isoformat()
    with jobs.use_cron_store(tmp_path / 'cron'):
        stored = jobs.create_job(prompt='test', schedule='every 4h')
        rows = jobs.load_jobs()
        rows[0]['next_run_at'] = slot
        jobs.save_jobs(rows)
        completed = executions.create_execution(stored['id'], source='builtin', scheduled_instant=slot)
        executions.finish_execution(completed['id'], success=True)

        with caplog.at_level(logging.WARNING, logger='cron.occurrences'):
            assert jobs.get_due_jobs() == []

        assert jobs.load_jobs()[0]['next_run_at'] != slot
    skip = [r.getMessage() for r in caplog.records if completed['id'] in r.getMessage()]
    assert len(skip) == 1, caplog.text
    assert stored['id'] in skip[0]
    from cron.occurrences import scheduled_instant
    assert scheduled_instant(slot) in skip[0]
