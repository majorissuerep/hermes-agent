"""Update safety nets read canonical and legacy snapshots without plaintext copies."""

import pytest

from hermes_security import io as state_io, vault
from tests.e2e.vault_home import seal_fixture_home


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    seal_fixture_home(home)
    yield home
    vault.lock_now(home)


def test_encrypted_snapshot_recovers_lost_jobs_and_model_route(home):
    from hermes_cli import backup
    from hermes_cli.config_effective import load_user_config_effective

    jobs = home / "cron/jobs.json"
    original = {"jobs": [{"id": "retained-job", "prompt": "Keep this scheduled task"}]}
    state_io.write_json(jobs, original, purpose="state")
    state_io.write_text(home / "config.yaml", "model:\n  provider: custom\n  default: retained-model\n",
                        purpose="config")
    snapshot = backup.create_quick_snapshot(hermes_home=home, label="pre-update")
    assert snapshot
    directory = home / "state-snapshots" / snapshot
    before = {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    state_io.write_json(jobs, {"jobs": []}, purpose="state")
    state_io.write_text(home / "config.yaml", "model:\n  provider: openai\n  default: wrong-model\nupdates:\n  check: false\n",
                        purpose="config")

    recovered = backup.restore_cron_jobs_if_emptied(snapshot, hermes_home=home)
    assert recovered and recovered["job_count"] == 1
    assert state_io.read_json(jobs, purpose="state") == original
    assert backup.restore_config_model_settings_if_rewritten(snapshot, hermes_home=home)
    config = load_user_config_effective()
    assert config["model"]["provider"] == "custom"
    assert config["model"]["default"] == "retained-model"
    assert config["updates"]["check"] is False
    assert {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*") if p.is_file()} == before
    assert jobs.read_bytes().startswith(b"HRMVAULT\x00")
    assert b"retained-model" not in (home / "config.yaml").read_bytes()


@pytest.mark.parametrize("legacy_layout", [False, True])
def test_encrypted_prompt_recovery_preserves_live_edits_and_locked_state(home, legacy_layout):
    from hermes_cli.backup import create_quick_snapshot
    from hermes_cli.backup_cron_prompts import restore_cron_prompt_fields_if_degraded
    from hermes_security.errors import VaultLockedError

    jobs = home / "cron/jobs.json"
    original = {"jobs": [{"id": "retained-job", "name": "A name", "prompt": "The actual instruction"}]}
    state_io.write_json(jobs, original, purpose="state")
    snapshot = create_quick_snapshot(hermes_home=home, label="pre-update")
    assert snapshot
    directory = home / "state-snapshots" / snapshot
    if legacy_layout:
        # Whole-home migration sealed old recovery points at their storage paths.
        state_io.write_json(directory / "cron/jobs.json", original, purpose="state")
        state_io.write_json(directory / "manifest.json", {"id": snapshot}, purpose="state")
    snapshot_bytes = (directory / "cron/jobs.json").read_bytes()
    changed = {"jobs": [{"id": "retained-job", "name": "A name", "prompt": "A name",
                         "enabled": False, "operator_note": "Preserve my edit"}]}
    state_io.write_json(jobs, changed, purpose="state")

    recovered = restore_cron_prompt_fields_if_degraded(snapshot, hermes_home=home)
    assert recovered and recovered["prompts"] == 1
    [job] = state_io.read_json(jobs, purpose="state")["jobs"]
    assert job["prompt"] == "The actual instruction"
    assert job["enabled"] is False and job["operator_note"] == "Preserve my edit"
    assert (directory / "cron/jobs.json").read_bytes() == snapshot_bytes
    before = jobs.read_bytes()
    vault.lock_now(home)
    with pytest.raises(VaultLockedError):
        restore_cron_prompt_fields_if_degraded(snapshot, hermes_home=home)
    assert jobs.read_bytes() == before
