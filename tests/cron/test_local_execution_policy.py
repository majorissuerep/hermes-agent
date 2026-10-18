"""Terminal cron rejects retired transports before any execution work."""
import pytest


@pytest.mark.parametrize("fields", [
    {"deliver": "origin"}, {"deliver": "all"}, {"deliver": "telegram:123"},
    {"deliver": "bot-chat"}, {"deliver": ["local", "origin"]},
    {"failure_deliver": "origin"}, {"attach_to_session": True},
])
def test_retired_destination_never_prepares_or_executes(monkeypatch, fields):
    from cron import scheduler

    def unexpected(*args, **kwargs):
        pytest.fail("unsupported delivery reached prompt preparation or execution")

    monkeypatch.setattr(scheduler, "_prepare_job_prompt", unexpected)
    success, output, response, error = scheduler.run_job({"id": "retired", **fields})
    assert not success
    assert "unsupported" in error
    assert output == error
    assert response == ""


def test_local_policy_allows_only_local_results():
    from cron.scheduler_delivery import local_delivery_error

    assert local_delivery_error({}) is None
    assert local_delivery_error({"deliver": "local", "failure_deliver": "local"}) is None


def test_local_script_runs_and_result_is_persisted(tmp_path, monkeypatch):
    from cron import jobs, scheduler

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_security import vault
    vault.init_vault(tmp_path, "synthetic-local-cron-password")
    vault.unlock(tmp_path, "synthetic-local-cron-password")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "local.sh"
    from hermes_security.io import write_text
    write_text(script, "#!/bin/sh\nprintf 'local-result-unique\\n'\n", purpose="state")
    script.chmod(0o700)
    job = jobs.create_job(prompt="", schedule="every 1h", script="local.sh", no_agent=True)
    assert job["deliver"] == "local"
    success, output, _response, error = scheduler.run_job(job)
    assert success, error
    assert "local-result-unique" in output
    saved = jobs.save_job_output(job["id"], output)
    from hermes_security.io import read_text
    assert "local-result-unique" in read_text(saved, purpose="state")
    assert b"local-result-unique" not in saved.read_bytes()
    assert saved.read_bytes().startswith(b"HRMVAULT\x00")
    assert jobs.claim_job_for_fire(job["id"])
    assert not jobs.claim_job_for_fire(job["id"])
