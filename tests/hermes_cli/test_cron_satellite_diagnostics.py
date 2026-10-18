"""Local persisted cron failure and dispatch health diagnostics."""
from datetime import datetime, timedelta, UTC
import pytest

@pytest.fixture
def served_root(tmp_path, monkeypatch):
    from cron import jobs
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(jobs, "CRON_DIR", home / "cron")
    monkeypatch.setattr(jobs, "JOBS_FILE", home / "cron/jobs.json")
    monkeypatch.setattr(jobs, "OUTPUT_DIR", home / "cron/output")
    return home

@pytest.mark.parametrize("detail", ["unreachable " * 30 + "\nsecret second line", ""])
def test_doctor_bounds_persisted_fire_errors(served_root, capsys, detail):
    from cron import jobs
    from hermes_cli.cron import cron_doctor

    jobs.create_job(prompt="probe", schedule="every 1h")
    records = jobs.load_jobs()
    records[0]["last_fire_error"] = {"at": "test-time", "detail": detail}
    jobs.save_jobs(records)
    assert cron_doctor() == bool(detail)
    output = capsys.readouterr().out
    if detail:
        assert "missed scheduled fire at test-time: unreachable" in output
        line = next(line for line in output.splitlines() if "missed scheduled fire at" in line)
        assert len(line.split(". Inspect the local cron logs")[0]) < 200
        assert f"hermes cron run {records[0]['id']}" in line
        assert detail not in output
        assert "secret second line" not in output
    else:
        assert "missed scheduled fire" not in output


@pytest.mark.parametrize("dispatch", ["catch_up", "late"])
def test_doctor_reports_persisted_dispatch_health(served_root, capsys, dispatch):
    from cron import jobs
    from hermes_cli.cron import cron_doctor

    job = jobs.create_job(prompt="probe", schedule="every 1h")
    if dispatch == "forward_error":
        jobs.note_fire_forward_failure(job["id"], "loopback unavailable")
    else:
        records = jobs.load_jobs()
        delay = timedelta(hours=5) if dispatch == "catch_up" else timedelta(minutes=6)
        records[0]["next_run_at"] = (datetime.now(UTC) - delay).isoformat()
        jobs.save_jobs(records)
        assert len(jobs.get_due_jobs()) == 1
        persisted = jobs.get_job(job["id"])
        assert persisted is not None
        assert persisted["last_dispatch"]["kind"] == dispatch
    assert cron_doctor() == 1
    output = capsys.readouterr().out
    expected = {"catch_up": "catch-up", "late": "last fire was late", "forward_error": "loopback unavailable"}
    assert expected[dispatch] in output
    assert "Review the findings above, then run `hermes cron doctor` again." in output
    jobs.mark_job_run(job["id"], success=True)
    if dispatch == "forward_error":
        assert cron_doctor() == 0
    else:
        assert "This warning clears at the next on-time fire." in output
        assert cron_doctor() == 1
        capsys.readouterr()
        records = jobs.load_jobs()
        records[0]["next_run_at"] = datetime.now(UTC).isoformat()
        jobs.save_jobs(records)
        assert len(jobs.get_due_jobs()) == 1
        persisted = jobs.get_job(job["id"])
        assert persisted is not None
        assert persisted["last_dispatch"]["kind"] == "on_time"
        assert cron_doctor() == 0
        assert "This warning clears" not in capsys.readouterr().out
