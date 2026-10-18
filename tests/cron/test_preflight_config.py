"""Cron pre-dispatch configuration validation (T1-26).

A job whose configuration cannot possibly produce a successful run — missing
provider API key, unready attached skill (missing required env), unknown
delivery platform — must be blocked BEFORE any agent machinery is constructed:

  - ``last_status`` becomes ``blocked_config`` (not a generic ``error``),
  - exactly ONE alert is delivered (no re-alert every tick — same
    alert-once spirit as the dead-pin auto-pause in #73506),
  - the agent is NEVER constructed, so no LLM call is burned.

``cron.preflight: false`` in config.yaml restores the old behavior (the run
proceeds to resolution and fails loudly every tick).

Related precedent: #27948 (fail-loud for hidden tools — same fail-before-run
spirit, different check).
"""

import json
import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import cron.jobs as cron_jobs
from cron.scheduler import run_job
import cron.scheduler as sched



_RUNTIME = {
    "api_key": "test-key",
    "base_url": "https://example.invalid/v1",
    "provider": "openrouter",
    "api_mode": "chat_completions",
}


def _job(**overrides):
    job = {
        "id": "pf-test",
        "name": "preflight test",
        "prompt": "hello",
        "enabled": True,
        "state": "scheduled",
        "schedule": {"kind": "interval", "minutes": 5, "display": "every 5m"},
        "deliver": "local",
        "model": "test-model",
        "provider": None,
        "base_url": None,
    }
    job.update(overrides)
    return job


class _AuthErrorFactory:
    """Raise a real AuthError from hermes_cli.auth."""

    def __call__(self, **kwargs):
        from hermes_cli.auth import AuthError

        raise AuthError("No API key configured for provider 'openrouter'")


def _run_job_patched(job, tmp_path, *, resolve=None, skill_view=None):
    """Drive run_job with the standard cron-test seams patched.

    Returns (success, output, final_response, error, agent_constructed).
    """
    patches = [
        patch("cron.scheduler._hermes_home", tmp_path),
        patch("hermes_cli.env_loader.load_hermes_dotenv"),
        patch("hermes_cli.env_loader.reset_secret_source_cache"),
        patch("tools.mcp_tool_discovery.discover_mcp_tools", return_value=[]),
    ]
    if resolve is None:
        patches.append(
            patch(
                "hermes_cli.runtime_provider.resolve_runtime_provider",
                return_value=dict(_RUNTIME),
            )
        )
    else:
        patches.append(
            patch(
                "hermes_cli.runtime_provider.resolve_runtime_provider",
                side_effect=resolve,
            )
        )
    if skill_view is not None:
        patches.append(patch("tools.skills_tool.skill_view", side_effect=skill_view))

    with patch("run_agent.AIAgent") as mock_agent_cls:
        mock_agent = MagicMock()
        mock_agent.run_conversation.return_value = {"final_response": "ok"}
        mock_agent_cls.return_value = mock_agent
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            success, output, final_response, error = run_job(job)
        agent_constructed = mock_agent_cls.called
    return success, output, final_response, error, agent_constructed


class TestMissingProviderKeyBlocks:
    def test_missing_key_blocked_config_no_agent(self, tmp_path):
        """Missing provider key (AuthError, no fallback chain) → blocked_config,
        agent never constructed, no LLM run burned."""
        job = _job()
        with cron_jobs.use_cron_store(tmp_path):
            cron_jobs.save_jobs([job])
            success, output, _final_response, error, agent_constructed = \
                _run_job_patched(job, tmp_path, resolve=_AuthErrorFactory())

        assert agent_constructed is False
        assert success is False
        assert error is not None
        assert "[blocked_config]" in error
        assert "blocked" in output.lower() or "BLOCKED" in output


    def test_fallback_chain_rescues_missing_primary_key(self, tmp_path):
        """A configured fallback chain means a missing primary key does NOT
        block — the existing auth-fallback path handles it."""
        (tmp_path / "config.yaml").write_text(
            "fallback_providers:\n"
            "  - provider: openrouter\n"
            "    model: z-ai/glm-5.2\n",
            encoding="utf-8",
        )
        calls = []

        def resolve(**kwargs):
            calls.append(kwargs.get("requested"))
            if kwargs.get("requested") in (None, ""):
                from hermes_cli.auth import AuthError

                raise AuthError("no key")
            return {**_RUNTIME, "provider": "openrouter"}

        # Unpinned: a job carrying its own model is pinned (#100437) and gets no chain.
        job = _job(model=None)
        with cron_jobs.use_cron_store(tmp_path):
            cron_jobs.save_jobs([job])
            success, _output, _final_response, error, agent_constructed = \
                _run_job_patched(job, tmp_path, resolve=resolve)

        assert agent_constructed is True
        assert success is True
        assert error is None

    def test_global_chain_does_not_rescue_a_pinned_job(self, tmp_path):
        """A pinned job never walks the global chain (#100437), so the chain must not skip the
        missing-key check for it either: block before the agent is built."""
        (tmp_path / "config.yaml").write_text(
            "fallback_providers:\n"
            "  - provider: openrouter\n"
            "    model: z-ai/glm-5.2\n",
            encoding="utf-8",
        )
        calls = []

        def resolve(**kwargs):
            calls.append(kwargs.get("requested"))
            if kwargs.get("requested") == "anthropic":
                from hermes_cli.auth import AuthError

                raise AuthError("no key")
            return {**_RUNTIME, "provider": kwargs.get("requested")}

        job = _job(provider="anthropic", model="claude-sonnet-5")
        with cron_jobs.use_cron_store(tmp_path):
            cron_jobs.save_jobs([job])
            success, _output, _final_response, error, agent_constructed = \
                _run_job_patched(job, tmp_path, resolve=resolve)

        assert agent_constructed is False
        assert success is False
        assert "provider credential missing" in (error or "")
        assert "openrouter" not in calls


class TestHealthyJobUnaffected:
    def test_healthy_job_runs_normally(self, tmp_path):
        job = _job()
        with cron_jobs.use_cron_store(tmp_path):
            cron_jobs.save_jobs([job])
            success, _output, final_response, error, agent_constructed = \
                _run_job_patched(job, tmp_path)

        assert success is True
        assert error is None
        assert final_response == "ok"
        assert agent_constructed is True

    def test_recovery_clears_alert_marker(self, tmp_path):
        """After a blocked tick, a healthy tick clears the alert-dedup marker
        so a FUTURE config break re-alerts."""
        job = _job()
        with cron_jobs.use_cron_store(tmp_path):
            cron_jobs.save_jobs([job])
            # Tick 1: blocked.
            _run_job_patched(job, tmp_path, resolve=_AuthErrorFactory())
            stored = next(j for j in cron_jobs.load_jobs() if j["id"] == job["id"])
            assert stored.get("preflight_alerted")
            # Tick 2: key restored → healthy run clears the marker.
            fresh = next(j for j in cron_jobs.load_jobs() if j["id"] == job["id"])
            success, *_rest, agent_constructed = _run_job_patched(fresh, tmp_path)
            assert success is True
            assert agent_constructed is True
            stored = next(j for j in cron_jobs.load_jobs() if j["id"] == job["id"])
            assert not stored.get("preflight_alerted")


class TestOptOut:
    def test_preflight_false_still_rejects_retired_delivery(self, tmp_path):
        (tmp_path / "config.yaml").write_text("cron:\n  preflight: false\n")
        success, _output, response, error = run_job(_job(failure_deliver="slack:alerts"))
        assert not success and response == ""
        assert "unsupported" in error


class TestSkillReadiness:
    def test_unready_skill_blocks(self, tmp_path):
        """An attached skill whose readiness_status is setup_needed (missing
        required env) blocks the run before the agent is constructed."""
        payload = json.dumps(
            {
                "success": True,
                "content": "# needy skill\nbody",
                "readiness_status": "setup_needed",
                "setup_needed": True,
                "missing_required_environment_variables": ["NEEDY_API_KEY"],
                "missing_required_commands": [],
            }
        )

        def fake_skill_view(name, *args, **kwargs):
            return payload

        job = _job(skills=["needy-skill"])
        with cron_jobs.use_cron_store(tmp_path):
            cron_jobs.save_jobs([job])
            success, output, _final_response, error, agent_constructed = \
                _run_job_patched(job, tmp_path, skill_view=fake_skill_view)

        assert agent_constructed is False
        assert success is False
        assert error is not None and "[blocked_config]" in error
        assert "NEEDY_API_KEY" in f"{error} {output}"

    def test_ready_skill_runs(self, tmp_path):
        payload = json.dumps(
            {
                "success": True,
                "content": "# ready skill\nbody",
                "readiness_status": "available",
                "setup_needed": False,
                "missing_required_environment_variables": [],
            }
        )

        def fake_skill_view(name, *args, **kwargs):
            return payload

        job = _job(skills=["ready-skill"])
        with cron_jobs.use_cron_store(tmp_path):
            cron_jobs.save_jobs([job])
            success, _output, _final_response, _error, agent_constructed = \
                _run_job_patched(job, tmp_path, skill_view=fake_skill_view)

        assert success is True
        assert agent_constructed is True
