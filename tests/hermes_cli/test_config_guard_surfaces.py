"""Local scheduled execution refuses corrupt config before starting an agent.

Public terminal command guards are covered by test_noninteractive_config_guard;
there is no separate gateway or service startup surface.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_config_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_IGNORE_USER_CONFIG", raising=False)


def _job(**overrides):
    return {"id": "job-test-1", "name": "guard test", "prompt": "hi", **overrides}


def test_local_run_job_fails_closed_without_overwriting_corrupt_config(tmp_path):
    from cron.scheduler import run_job

    config = tmp_path / "config.yaml"
    before = b"model: [unterminated\n"
    config.write_bytes(before)
    success, output_doc, final_response, error = run_job(_job())
    assert success is False
    assert "Hermes stopped because your settings file" in error
    assert "config.yaml" in error and "Error:" in output_doc
    assert final_response == ""
    assert config.read_bytes() == before


def test_local_script_only_job_does_not_require_model_config(tmp_path, monkeypatch):
    from cron import scheduler

    config = tmp_path / "config.yaml"
    before = b"model: [unterminated\n"
    config.write_bytes(before)
    calls = []
    expected = (True, "local script output", "", None)
    def script_only(job, *_args):
        calls.append(job["id"])
        return expected
    monkeypatch.setattr(scheduler, "_run_no_agent_job", script_only)
    assert scheduler.run_job(_job(no_agent=True, script="true")) == expected
    assert calls == ["job-test-1"]
    assert config.read_bytes() == before
