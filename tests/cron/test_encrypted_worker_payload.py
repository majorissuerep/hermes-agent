"""Real child adoption opens a sealed fire without persisting an unlock password."""
import json
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("grant", [True, False])
def test_encrypted_worker_adoption_and_failure_cleanup(tmp_path, monkeypatch, grant):
    from cron import executions, jobs
    from hermes_security import vault
    from hermes_security.io import read_text, write_json
    from hermes_security.session_authority import start_session_authority, session_environment

    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    vault.init_vault(home, "synthetic-worker-unlock-password")
    vault.unlock(home, "synthetic-worker-unlock-password")
    scripts = home / "scripts"
    scripts.mkdir()
    script = scripts / "effect.sh"
    from hermes_security.io import write_text
    write_text(script, "#!/bin/sh\nprintf 'encrypted-worker-result-unique\\n'\n", purpose="state")
    script.chmod(0o700)
    with jobs.use_cron_store(home):
        job = jobs.create_job(prompt="", schedule="every 1h", script="effect.sh", no_agent=True)
        assert jobs.claim_job_for_fire(job["id"])
        job = jobs.get_job(job["id"])
        row = executions.create_execution(job["id"], source="direct")
        job["execution_id"] = row["id"]
        assert executions.mark_execution_handoff_pending(row["id"])
        payload, ack = home / "fire.json", home / "fire.ready"
        write_json(payload, {"job": job, "profile_home": str(home)}, purpose="state")
        assert payload.read_bytes().startswith(b"HRMVAULT\x00")
        assert b"effect.sh" not in payload.read_bytes()
        authority = start_session_authority(home, row["id"])
        assert authority is not None
        env = {k: v for k, v in os.environ.items() if not k.startswith(("HERMES_", "_HERMES_"))}
        env.update(HERMES_HOME=str(home), HERMES_SESSION_ID=row["id"])
        if grant:
            env.update(session_environment(home, row["id"]))
        try:
            child = subprocess.run(
                [sys.executable, "-c", "from pathlib import Path; import sys; from cron.scheduler import _run_external_worker_payload; sys.exit(0 if _run_external_worker_payload(Path(sys.argv[1]),Path(sys.argv[2])) else 7)", str(payload), str(ack)],
                env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
                check=False,
            )
        finally:
            authority.close()
        assert not payload.exists()
        if not grant:
            assert child.returncode == 7
            assert not ack.exists()
            assert not list(jobs.get_cron_output_dir().glob("*/*.md"))
            return
        assert child.returncode == 0, child.stdout + child.stderr
        assert json.loads(ack.read_text())["execution_id"] == row["id"]
        assert executions.get_execution(row["id"])["status"] == "completed"
        output = next(jobs.get_cron_output_dir().glob("*/*.md"))
        assert b"encrypted-worker-result-unique" not in output.read_bytes()
        assert "encrypted-worker-result-unique" in read_text(output, purpose="state")
        assert "synthetic-worker-unlock-password" not in json.dumps(env)
