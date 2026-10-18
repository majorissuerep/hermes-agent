"""Tests for cron/scheduler.py — origin resolution, delivery routing, and error logging."""

import asyncio
import contextlib
import contextvars
import itertools
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from cron.scheduler import (
    SILENT_MARKER,
    _build_job_prompt,
    _merge_mcp_into_per_job_toolsets,
    _run_cron_cleanup_with_timeout,
    _resolve_cron_enabled_toolsets,
    _summarize_cron_failure_for_delivery,
)
from tools.env_passthrough import clear_env_passthrough


def _run_owned_job(job, tmp_path, db=None):
    """Exercise the real direct local scheduler execution path."""
    from cron.scheduler import run_job
    return run_job(job)


def test_cron_cleanup_worker_inherits_caller_contextvars():
    """Profile-scoped secrets must remain visible during threaded cleanup."""
    profile_scope = contextvars.ContextVar("test_cron_cleanup_profile_scope")
    profile_scope.set("profile-key")
    observed = []

    assert _run_cron_cleanup_with_timeout(
        lambda: observed.append(profile_scope.get(None)),
        job_id="context-scope",
        label="test cleanup",
        timeout_seconds=1,
    )
    assert observed == ["profile-key"]

class TestSummarizeCronFailureForDelivery:
    def test_embedded_429_in_source_identifier_is_not_a_rate_limit(self):
        summary = _summarize_cron_failure_for_delivery(
            {"name": "LLM Wiki Incremental Index", "no_agent": True},
            "Script failed: path/hash429abc.md source snapshot failure",
        )

        assert "rate-limited" not in summary
        assert "hash429abc.md" in summary

    def test_http_429_is_still_classified_as_a_rate_limit(self):
        summary = _summarize_cron_failure_for_delivery(
            {"name": "provider-backed job"},
            "HTTP 429: Too Many Requests",
        )

        assert "rate-limited" in summary
        # Chain wording is honest (#85508): either "no backup provider succeeded" (chain
        # configured) or the "no backup provider is configured" guidance.
        assert "backup provider" in summary.lower()

    def test_no_agent_rate_limit_does_not_claim_a_fallback_chain(self):
        summary = _summarize_cron_failure_for_delivery(
            {"name": "script job", "no_agent": True},
            "HTTP 429: Too Many Requests",
        )

        # Composed with #77648: a no_agent job never gets provider-shaped
        # classification at all — the generic cleaner reports the script's
        # own error instead.
        assert "ai model service" not in summary.lower()
        assert "backup provider" not in summary.lower()

    def test_no_agent_timeout_is_identified_as_a_script_timeout(self):
        summary = _summarize_cron_failure_for_delivery(
            {"name": "script job", "no_agent": True},
            "Script timed out after 3600s",
        )

        assert "script timed out" in summary
        assert "did not respond in time" not in summary
        assert "backup provider" not in summary.lower()

class TestPerJobToolsetMcpMerge:
    """A per-job enabled_toolsets allowlist must not silently drop MCP servers."""

    CFG = {
        "mcp_servers": {
            "finnhub": {"enabled": True},
            "playwright": {"enabled": True},
            "disabled_one": {"enabled": False},
            "string_enabled": {"enabled": "true"},
            "not_a_dict": "ignored",
        }
    }

    def _enabled_names(self):
        return {"finnhub", "playwright", "string_enabled"}

    def test_native_only_list_gets_all_enabled_mcp_servers(self):
        result = _merge_mcp_into_per_job_toolsets(["web", "terminal"], self.CFG)
        assert result[:2] == ["web", "terminal"]
        assert set(result) == {"web", "terminal"} | self._enabled_names()

    def test_explicit_mcp_name_is_treated_as_allowlist(self):
        # User named one server -> add nothing further.
        result = _merge_mcp_into_per_job_toolsets(["web", "finnhub"], self.CFG)
        assert result == ["web", "finnhub"]
        assert "playwright" not in result

    def test_no_mcp_sentinel_opts_out_and_is_stripped(self):
        result = _merge_mcp_into_per_job_toolsets(["web", "no_mcp"], self.CFG)
        assert result == ["web"]
        assert not (set(result) & self._enabled_names())

    def test_resolver_keeps_memory_in_per_job_list(self):
        result = _resolve_cron_enabled_toolsets(
            {"enabled_toolsets": ["memory", "file"]},
            {"mcp_servers": {}},
        )
        assert "memory" in result
        assert "file" in result

    def test_resolver_keeps_memory_from_platform_fallback(self):
        job = {"enabled_toolsets": None}
        with patch(
            "hermes_cli.tools_config._get_platform_tools",
            return_value={"web", "memory", "file"},
        ):
            result = _resolve_cron_enabled_toolsets(job, {})
        assert result == ["file", "memory", "web"]

    def test_resolver_failure_fails_closed_instead_of_every_toolset(self):
        """An unreadable cron-platform restriction must not become ``None`` (= all toolsets,
        #111380): the resolver raises and run_job records the failure. A malformed
        ``platform_toolsets`` block is the real-world trigger, so no patching of the resolver."""
        job = {"enabled_toolsets": None}
        with pytest.raises(RuntimeError, match="toolset resolution failed"):
            _resolve_cron_enabled_toolsets(job, {"platform_toolsets": "oops"})
        # Per-job lists never touch the platform resolver: an unknown name still resolves.
        assert _resolve_cron_enabled_toolsets(
            {"enabled_toolsets": ["nonexistent_ts"]}, {"platform_toolsets": "oops", "mcp_servers": {}}
        ) == ["nonexistent_ts"]






class TestRunJobSessionPersistence:
    def test_run_job_passes_session_db_and_cron_platform(self, tmp_path):
        job = {
            "id": "test-job",
            "name": "test",
            "prompt": "hello",
        }
        fake_db = MagicMock()
        fake_db.get_compression_tip.side_effect = lambda session_id: session_id

        with patch("cron.scheduler._hermes_home", tmp_path), \
             patch("hermes_cli.env_loader.load_hermes_dotenv"), \
             patch("hermes_cli.env_loader.reset_secret_source_cache"), \
             patch("hermes_state.registry.acquire", return_value=fake_db), \
             patch(
                 "hermes_cli.runtime_provider.resolve_runtime_provider",
                 return_value={
                     "api_key": "test-key",
                     "base_url": "https://example.invalid/v1",
                     "provider": "openrouter",
                     "api_mode": "chat_completions",
                 },
             ), \
             patch("run_agent.AIAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run_conversation.return_value = {"final_response": "ok"}
            mock_agent_cls.return_value = mock_agent

            success, output, final_response, error = _run_owned_job(job, tmp_path, fake_db)

        assert success is True
        assert error is None
        assert final_response == "ok"
        assert "ok" in output

        kwargs = mock_agent_cls.call_args.kwargs
        assert kwargs["session_db"] is fake_db
        assert kwargs["platform"] == "cron"
        assert kwargs["session_id"].startswith("cron_test-job_")
        original_session_id = kwargs["session_id"]
        fake_db.get_compression_tip.assert_called_once_with(original_session_id)
        fake_db.end_session.assert_called_once()
        call_args = fake_db.end_session.call_args
        assert call_args[0][0] == original_session_id
        assert call_args[0][1] == "cron_complete"
        fake_db.close.assert_called_once()
        mock_agent.close.assert_called_once()

    def test_run_job_disarms_agent_close_after_scheduler_finalizes_session(self, tmp_path):
        """Cron owns the terminal session reason; agent.close must not end it twice.

        Regression for the #94736 teardown warning seen after every healthy cron
        run: the scheduler closed its shared SessionDB, then AIAgent.close() tried
        another end_session("agent_close"), forcing SessionDB to reopen solely
        for a redundant write.
        """
        job = {"id": "single-finalize", "name": "test", "prompt": "hello"}
        fake_db = MagicMock()
        fake_db.get_compression_tip.side_effect = lambda session_id: session_id
        closed = False
        calls_after_close = []

        def close_db():
            nonlocal closed
            closed = True

        def end_session(*args):
            if closed:
                calls_after_close.append(args)

        fake_db.close.side_effect = close_db
        fake_db.end_session.side_effect = end_session

        with patch("cron.scheduler._hermes_home", tmp_path), \
             patch("hermes_cli.env_loader.load_hermes_dotenv"), \
             patch("hermes_cli.env_loader.reset_secret_source_cache"), \
             patch("hermes_state.registry.acquire", return_value=fake_db), \
             patch(
                 "hermes_cli.runtime_provider.resolve_runtime_provider",
                 return_value={
                     "api_key": "test-key",
                     "base_url": "https://example.invalid/v1",
                     "provider": "openrouter",
                     "api_mode": "chat_completions",
                 },
             ), \
             patch("run_agent.AIAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run_conversation.return_value = {"final_response": "ok"}

            def close_agent():
                if mock_agent._end_session_on_close:
                    fake_db.end_session(mock_agent.session_id, "agent_close")

            mock_agent.close.side_effect = close_agent
            mock_agent_cls.return_value = mock_agent
            success, *_ = _run_owned_job(job, tmp_path, fake_db)

        assert success is True
        assert fake_db.end_session.call_count == 1
        assert calls_after_close == []

    @contextlib.contextmanager
    def _run_job_patches(self, tmp_path, extra=()):
        """Apply every patch run_job tests need, as one bundle.

        Yields ``(fake_db, mock_agent_cls)``. Using an ExitStack that enters
        the whole list means a caller can never silently drop a patch by
        index — the previous positional-list form let a seam split shift
        ``resolve_runtime_provider`` off the end of the applied slice, so the
        real resolver ran and (only on a dev machine with ambient creds) hid
        an auth failure that CI then caught. Every test enters all patches.

        ``extra`` is an iterable of additional context managers (e.g. a
        per-test ``_get_platform_tools`` patch) entered alongside the base set.
        """
        fake_db = MagicMock()
        mock_agent = MagicMock()
        mock_agent.run_conversation.return_value = {"final_response": "ok"}
        base = [
            patch("cron.scheduler._hermes_home", tmp_path),
            patch("hermes_cli.env_loader.load_hermes_dotenv"),
            patch("hermes_cli.env_loader.reset_secret_source_cache"),
            patch("hermes_state.registry.acquire", return_value=fake_db),
            patch(
                "hermes_cli.runtime_provider.resolve_runtime_provider",
                return_value={
                    "api_key": "test-key",
                    "base_url": "https://example.invalid/v1",
                    "provider": "openrouter",
                    "api_mode": "chat_completions",
                },
            ),
            patch("run_agent.AIAgent", return_value=mock_agent),
        ]
        with contextlib.ExitStack() as stack:
            entered = [stack.enter_context(cm) for cm in base]
            for cm in extra:
                stack.enter_context(cm)
            mock_agent_cls = entered[-1]  # the AIAgent patch
            yield fake_db, mock_agent_cls

    def test_run_job_memory_enabled_in_cron(self, tmp_path):
        """Cron agents get memory like any other agent run.

        skip_memory=False and the memory toolset is not policy-denied, so
        MEMORY.md/USER.md load and the memory tool follows normal toolset
        resolution.
        """
        job = {
            "id": "memory-enabled-job",
            "name": "test",
            "prompt": "hello",
        }
        with self._run_job_patches(tmp_path) as (fake_db, mock_agent_cls):
            _run_owned_job(job, tmp_path, fake_db)

        kwargs = mock_agent_cls.call_args.kwargs
        assert kwargs["skip_memory"] is False
        assert "memory" not in (kwargs["disabled_toolsets"] or []), (
            "memory toolset must not be policy-denied in cron"
        )


    def test_tick_marks_empty_response_as_error(self, tmp_path):
        """When run_job returns success=True but final_response is empty,
        tick() should mark the job as error so last_status != 'ok'.
        (issue #8585)
        """
        from cron.scheduler import tick

        job = {
            "id": "empty-job",
            "name": "empty-test",
            "prompt": "do something",
            "schedule": "every 1h",
            "enabled": True,
            "next_run_at": "2020-01-01T00:00:00",
            "deliver": "local",
            "last_status": None,
        }

        with patch("cron.scheduler._hermes_home", tmp_path), \
             patch("cron.scheduler.get_due_jobs", return_value=[job]), \
             patch("cron.scheduler.claim_job_for_fire", return_value=True), \
             patch("cron.scheduler.mark_job_run") as mock_mark, \
             patch("cron.scheduler.save_job_output", return_value="/tmp/out.md"), \
             patch("cron.scheduler.run_job", return_value=(True, "output", "", None)):
            tick(verbose=False)

        # Should be called with success=False because final_response is empty
        mock_mark.assert_called_once()
        call_args = mock_mark.call_args
        assert call_args[0][0] == "empty-job"
        assert call_args[0][1] is False  # success should be False
        assert "empty" in call_args[0][2].lower()  # error should mention empty



    @pytest.mark.parametrize("timeout_value", ["600", "0"])
    def test_run_job_heartbeats_oneshot_claim_in_both_wait_modes(
        self, monkeypatch, timeout_value
    ):
        """Timed and unlimited owner-side monitors refresh their owned claim."""
        from concurrent.futures import Future
        from cron.scheduler import _run_agent_with_watchdog

        job = {
            "id": "heartbeat-job",
            "schedule": {"kind": "once", "run_at": "2026-07-10T12:00:00Z"},
            "run_claim": {"at": "2026-07-10T12:00:00Z", "by": "owner-token"},
        }
        fake_future = Future()
        fake_future.set_result({"final_response": "ok"})
        fake_pool = MagicMock()
        fake_pool.submit.return_value = fake_future
        wait_results = [(set(), set()), ({fake_future}, set())]
        monotonic_ticks = itertools.count(step=61.0)
        monkeypatch.setenv("HERMES_CRON_TIMEOUT", timeout_value)

        with patch("cron.scheduler.concurrent.futures.ThreadPoolExecutor", return_value=fake_pool), \
             patch("cron.scheduler.concurrent.futures.wait", side_effect=wait_results), \
             patch("cron.scheduler.time.monotonic", side_effect=monotonic_ticks.__next__), \
             patch("cron.scheduler.heartbeat_run_claim", return_value=True) as heartbeat:
            result = _run_agent_with_watchdog(
                MagicMock(), "hello", job, job["id"], "heartbeat", "cron:heartbeat-job:test", None)

        assert result["final_response"] == "ok"
        heartbeat.assert_called_once_with("heartbeat-job", expected_owner="owner-token")

    def test_run_job_resets_secret_source_cache_before_reload(self, tmp_path, monkeypatch):
        """Each run must clear the secret-source cache before re-reading the
        env, so a long-running gateway re-resolves Bitwarden/BSM-backed secrets
        instead of leaving the startup .env placeholder in place (#33465).

        A bare ``load_dotenv`` re-load can't do this: startup already recorded
        this HERMES_HOME in ``_APPLIED_HOMES``, so the external-secret pull
        no-ops and only the placeholder is re-applied. The scheduler must call
        ``reset_secret_source_cache()`` (forcing the re-pull) and route through
        ``load_hermes_dotenv`` (which then re-applies external secret sources).
        """
        job = {"id": "bsm-job", "name": "bsm", "prompt": "hello"}
        fake_db = MagicMock()
        call_order = []

        def _record_reset(*_args):
            call_order.append("reset")

        def _record_load(*args, **kwargs):
            call_order.append("load")
            return []

        with patch("cron.scheduler._hermes_home", tmp_path), \
             patch("hermes_cli.env_loader.reset_secret_source_cache", _record_reset), \
             patch("hermes_cli.env_loader.load_hermes_dotenv", _record_load), \
             patch("hermes_state.registry.acquire", return_value=fake_db), \
             patch(
                 "hermes_cli.runtime_provider.resolve_runtime_provider",
                 return_value={
                     "api_key": "***",
                     "base_url": "https://example.invalid/v1",
                     "provider": "openrouter",
                     "api_mode": "chat_completions",
                 },
             ), \
             patch("run_agent.AIAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run_conversation.return_value = {"final_response": "ok"}
            mock_agent_cls.return_value = mock_agent
            success, _output, _final, error = _run_owned_job(job, tmp_path, fake_db)

        assert success is True
        assert error is None
        # reset MUST precede the reload, else _APPLIED_HOMES no-ops the re-pull.
        assert call_order[:2] == ["reset", "load"], call_order


class TestRunJobConfigEnvVarExpansion:
    """Verify that ${VAR} references in config.yaml are expanded when running cron jobs."""

    _RUNTIME = {
        "api_key": "test-key",
        "base_url": "https://example.invalid/v1",
        "provider": "openrouter",
        "api_mode": "chat_completions",
    }

    def test_model_env_ref_in_config_yaml_is_expanded(self, tmp_path, monkeypatch):
        """${VAR} in config.yaml model: is expanded from the PROFILE's own secret scope. The owner
        bridge runs the job under that profile's runtime scope, where a process-env value would be
        another profile's leak, so the ref resolves from ``<home>/.env`` and never os.environ."""
        (tmp_path / "config.yaml").write_text("model: ${_HERMES_TEST_CRON_MODEL}\n")
        (tmp_path / ".env").write_text("_HERMES_TEST_CRON_MODEL=gpt-4o-mini-cron-test\n")
        monkeypatch.setenv("_HERMES_TEST_CRON_MODEL", "leaked-from-process-env")

        job = {"id": "env-job", "name": "env test", "prompt": "hi"}
        fake_db = MagicMock()

        with patch("cron.scheduler._hermes_home", tmp_path), \
             patch("hermes_cli.env_loader.load_hermes_dotenv"), \
             patch("hermes_cli.env_loader.reset_secret_source_cache"), \
             patch("hermes_state.registry.acquire", return_value=fake_db), \
             patch("hermes_cli.runtime_provider.resolve_runtime_provider",
                   return_value=self._RUNTIME), \
             patch("run_agent.AIAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run_conversation.return_value = {"final_response": "ok"}
            mock_agent_cls.return_value = mock_agent
            success, _, _, error = _run_owned_job(job, tmp_path, fake_db)

        assert success is True
        assert error is None
        kwargs = mock_agent_cls.call_args.kwargs
        assert kwargs["model"] == "gpt-4o-mini-cron-test", (
            f"Expected model='gpt-4o-mini-cron-test', got {kwargs['model']!r}. "
            "config.yaml ${VAR} was not expanded in the cron execution path."
        )

    def test_transient_dns_fallback_switches_provider_and_model_together(self, tmp_path):
        """DNS blip during primary OAuth resolve must still walk fallback_providers.

        Regression for Daily Focus Kickoff 2026-08-11: xai-oauth token refresh
        raised httpx.ConnectError ([Errno 8] nodename nor servname provided)
        and the scheduler only tried fallbacks on AuthError, so the job died
        before XAI_API_KEY / Anthropic could rescue it. The job follows the
        main model (unpinned): a pinned job never walks the global chain
        (#100437, tests/cron/test_cron_pinned_job_fallback.py).
        """
        import httpx

        (tmp_path / "config.yaml").write_text(
            "model:\n"
            "  default: grok-4.5\n"
            "  provider: xai-oauth\n"
            "fallback_providers:\n"
            "  - provider: xai\n"
            "    model: grok-4.5\n"
            "  - provider: anthropic\n"
            "    model: claude-opus-5\n",
            encoding="utf-8",
        )
        job = {
            "id": "dns-fallback",
            "name": "dns fallback",
            "prompt": "hi",
        }
        fake_db = MagicMock()
        requested = []

        def resolve_runtime(**kwargs):
            requested.append(kwargs.get("requested"))
            if kwargs.get("requested") in (None, "xai-oauth"):
                raise httpx.ConnectError(
                    "[Errno 8] nodename nor servname provided, or not known"
                )
            # First fallback rung (xai API key) succeeds.
            assert kwargs["requested"] == "xai"
            assert kwargs["target_model"] == "grok-4.5"
            return {**self._RUNTIME, "provider": "xai", "api_mode": "chat_completions"}

        with patch("cron.scheduler._hermes_home", tmp_path), \
             patch("hermes_cli.env_loader.load_hermes_dotenv"), \
             patch("hermes_cli.env_loader.reset_secret_source_cache"), \
             patch("hermes_state.registry.acquire", return_value=fake_db), \
             patch("hermes_cli.runtime_provider.resolve_runtime_provider",
                   side_effect=resolve_runtime), \
             patch("tools.mcp_tool_discovery.discover_mcp_tools", return_value=[]), \
             patch("run_agent.AIAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run_conversation.return_value = {"final_response": "ok"}
            mock_agent_cls.return_value = mock_agent
            success, _, _, error = _run_owned_job(job, tmp_path, fake_db)

        assert success is True, error
        assert error is None
        assert requested == [None, "xai"]
        kwargs = mock_agent_cls.call_args.kwargs
        assert kwargs["provider"] == "xai"
        assert kwargs["model"] == "grok-4.5"

    def test_auth_fallback_switches_provider_and_model_together(self, tmp_path):
        """Codex auth failure must produce OpenRouter+GLM, never OpenRouter+GPT (unpinned job:
        a pinned one does not walk the global chain, #100437)."""
        from hermes_cli.auth import AuthError

        (tmp_path / "config.yaml").write_text(
            "model:\n"
            "  default: gpt-5.6-sol\n"
            "  provider: openai-codex\n"
            "fallback_providers:\n"
            "  - provider: anthropic\n"
            "  - provider: openrouter\n"
            "    model: z-ai/glm-5.2\n",
            encoding="utf-8",
        )
        job = {
            "id": "auth-fallback",
            "name": "auth fallback",
            "prompt": "hi",
        }
        fake_db = MagicMock()
        requested = []

        def resolve_runtime(**kwargs):
            requested.append(kwargs.get("requested"))
            if kwargs.get("requested") in (None, "openai-codex"):
                raise AuthError("No Codex credentials stored")
            assert kwargs["requested"] == "openrouter"
            assert kwargs["target_model"] == "z-ai/glm-5.2"
            return {**self._RUNTIME, "provider": "openrouter"}

        with patch("cron.scheduler._hermes_home", tmp_path), \
             patch("hermes_cli.env_loader.load_hermes_dotenv"), \
             patch("hermes_cli.env_loader.reset_secret_source_cache"), \
             patch("hermes_state.registry.acquire", return_value=fake_db), \
             patch("hermes_cli.runtime_provider.resolve_runtime_provider",
                   side_effect=resolve_runtime), \
             patch("tools.mcp_tool_discovery.discover_mcp_tools", return_value=[]), \
             patch("run_agent.AIAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run_conversation.return_value = {"final_response": "ok"}
            mock_agent_cls.return_value = mock_agent
            success, _, _, error = _run_owned_job(job, tmp_path, fake_db)

        assert success is True
        assert error is None
        assert requested == [None, "openrouter"]
        kwargs = mock_agent_cls.call_args.kwargs
        assert kwargs["provider"] == "openrouter"
        assert kwargs["model"] == "z-ai/glm-5.2"

class TestRunJobModelResolution:
    """Verify defensive model resolution for jobs stored with ``model: null``.

    Issue #23979: a cron job created without an explicit model is stored as
    ``model: null``. At fire time the scheduler must:
      1. fall back to ``HERMES_MODEL`` env if set,
      2. else fall back to config.yaml ``model.default`` if set,
      3. else fail fast with an actionable error — never let an empty string
         reach the provider where it surfaces as an opaque 400.
    """

    _RUNTIME = {
        "api_key": "test-key",
        "base_url": "https://example.invalid/v1",
        "provider": "openrouter",
        "api_mode": "chat_completions",
    }

    def test_null_job_model_falls_back_to_env(self, tmp_path, monkeypatch):
        """``model: null`` on the job uses HERMES_MODEL when set."""
        (tmp_path / "config.yaml").write_text("")
        monkeypatch.setenv("HERMES_MODEL", "env-model")

        job = {"id": "null-model-job", "name": "null model", "prompt": "hi", "model": None}
        fake_db = MagicMock()

        with patch("cron.scheduler._hermes_home", tmp_path), \
             patch("hermes_cli.env_loader.load_hermes_dotenv"), \
             patch("hermes_cli.env_loader.reset_secret_source_cache"), \
             patch("hermes_state.registry.acquire", return_value=fake_db), \
             patch("hermes_cli.runtime_provider.resolve_runtime_provider",
                   return_value=self._RUNTIME), \
             patch("run_agent.AIAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run_conversation.return_value = {"final_response": "ok"}
            mock_agent_cls.return_value = mock_agent
            success, _, _, error = _run_owned_job(job, tmp_path, fake_db)

        assert success is True
        assert error is None
        assert mock_agent_cls.call_args.kwargs["model"] == "env-model"

    def test_no_model_anywhere_fails_with_actionable_error(self, tmp_path, monkeypatch):
        """All three sources empty → fail fast with a clear message, not an opaque 400."""
        (tmp_path / "config.yaml").write_text("")
        monkeypatch.delenv("HERMES_MODEL", raising=False)

        job = {"id": "no-model-job", "name": "no model anywhere", "prompt": "hi", "model": None}
        fake_db = MagicMock()

        with patch("cron.scheduler._hermes_home", tmp_path), \
             patch("hermes_cli.env_loader.load_hermes_dotenv"), \
             patch("hermes_cli.env_loader.reset_secret_source_cache"), \
             patch("hermes_state.registry.acquire", return_value=fake_db), \
             patch("hermes_cli.runtime_provider.resolve_runtime_provider",
                   return_value=self._RUNTIME), \
             patch("run_agent.AIAgent") as mock_agent_cls:
            success, _, _, error = _run_owned_job(job, tmp_path, fake_db)

        assert success is False
        assert error is not None
        # AIAgent must never be constructed with an empty model — that's
        # precisely the bug we're guarding against.
        mock_agent_cls.assert_not_called()

    def test_config_model_alias_key_resolves(self, tmp_path, monkeypatch):
        """A ``model: {model: ...}`` alias key resolves like the CLI sibling.

        ``hermes_cli/oneshot.py``, ``fallback_cmd.py`` and ``prompt_size.py``
        all accept ``model.model`` as an alias for ``model.default``. The cron
        resolver mirrors that so a config that works in the CLI also works in
        cron.
        """
        (tmp_path / "config.yaml").write_text("model:\n  model: alias-key-model\n")
        monkeypatch.delenv("HERMES_MODEL", raising=False)

        job = {"id": "alias-job", "name": "alias", "prompt": "hi", "model": None}
        fake_db = MagicMock()

        with patch("cron.scheduler._hermes_home", tmp_path), \
             patch("hermes_cli.env_loader.load_hermes_dotenv"), \
             patch("hermes_cli.env_loader.reset_secret_source_cache"), \
             patch("hermes_state.registry.acquire", return_value=fake_db), \
             patch("hermes_cli.runtime_provider.resolve_runtime_provider",
                   return_value=self._RUNTIME), \
             patch("run_agent.AIAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run_conversation.return_value = {"final_response": "ok"}
            mock_agent_cls.return_value = mock_agent
            success, _, _, error = _run_owned_job(job, tmp_path, fake_db)

        assert success is True
        assert error is None
        assert mock_agent_cls.call_args.kwargs["model"] == "alias-key-model"

    def test_corrupt_config_yaml_refuses_even_with_job_model(self, tmp_path, monkeypatch):
        """An explicit model cannot bypass invalid terminal/tool configuration."""
        (tmp_path / "config.yaml").write_text("{{{invalid yaml!!!")
        monkeypatch.delenv("HERMES_MODEL", raising=False)

        job = {"id": "corrupt-job", "name": "corrupt", "prompt": "hi", "model": "explicit-model"}
        fake_db = MagicMock()

        with patch("cron.scheduler._hermes_home", tmp_path), \
             patch("hermes_cli.env_loader.load_hermes_dotenv"), \
             patch("hermes_cli.env_loader.reset_secret_source_cache"), \
             patch("hermes_state.registry.acquire", return_value=fake_db), \
             patch("hermes_cli.runtime_provider.resolve_runtime_provider",
                   return_value=self._RUNTIME), \
             patch("run_agent.AIAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run_conversation.return_value = {"final_response": "ok"}
            mock_agent_cls.return_value = mock_agent
            success, _, _, error = _run_owned_job(job, tmp_path, fake_db)

        assert success is False
        assert "Hermes stopped because your settings file" in error
        mock_agent_cls.assert_not_called()

class TestRunJobSkillBacked:
    def test_run_job_preserves_skill_env_passthrough_into_worker_thread(self, tmp_path):
        job = {
            "id": "skill-env-job",
            "name": "skill env test",
            "prompt": "Use the skill.",
            "skill": "notion",
        }

        fake_db = MagicMock()

        def _skill_view(name):
            assert name == "notion"
            from tools.env_passthrough import register_env_passthrough

            register_env_passthrough(["NOTION_API_KEY"])
            return json.dumps({"success": True, "content": "# notion\nUse Notion."})

        def _run_conversation(prompt, *, task_id=None):
            from tools.env_passthrough import get_all_passthrough

            assert isinstance(task_id, str)
            assert task_id.startswith("cron:skill-env-job:")
            assert "NOTION_API_KEY" in get_all_passthrough()
            return {"final_response": "ok"}

        with patch("cron.scheduler._hermes_home", tmp_path), \
             patch("hermes_cli.env_loader.load_hermes_dotenv"), \
             patch("hermes_cli.env_loader.reset_secret_source_cache"), \
             patch("hermes_state.registry.acquire", return_value=fake_db), \
             patch(
                 "hermes_cli.runtime_provider.resolve_runtime_provider",
                 return_value={
                     "api_key": "***",
                     "base_url": "https://example.invalid/v1",
                     "provider": "openrouter",
                     "api_mode": "chat_completions",
                 },
             ), \
             patch("tools.skills_tool.skill_view", side_effect=_skill_view), \
             patch("run_agent.AIAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run_conversation.side_effect = _run_conversation
            mock_agent_cls.return_value = mock_agent

            try:
                success, _output, final_response, error = _run_owned_job(job, tmp_path, fake_db)
            finally:
                clear_env_passthrough()

        assert success is True
        assert error is None
        assert final_response == "ok"


class TestOneShotDispatchClaim:
    """run_one_job must claim a finite one-shot's dispatch BEFORE run_job so a
    tick that dies mid-execution can't re-fire it forever (issue #38758)."""

    def _oneshot(self):
        return {
            "id": "monitor-job",
            "name": "monitor",
            "deliver": "local",
            "origin": {"platform": "telegram", "chat_id": "123"},
            "schedule": {"kind": "once", "run_at": "2026-01-01T00:00:00+00:00"},
            "repeat": {"times": 1, "completed": 0},
        }

    def test_claim_runs_before_run_job(self):
        order = []
        with patch("cron.scheduler.get_due_jobs", return_value=[self._oneshot()]), \
             patch("cron.scheduler.claim_job_for_fire", return_value=True), \
             patch("cron.scheduler.claim_dispatch", side_effect=lambda _id: order.append("claim") or True), \
             patch("cron.scheduler.run_job", side_effect=lambda _j, **_kw: order.append("run") or (True, "# out", "ok", None)), \
             patch("cron.scheduler.save_job_output", return_value="/tmp/out.md"), \
             patch("cron.scheduler.mark_job_run"):
            from cron.scheduler import tick
            tick(verbose=False)
        assert order == ["claim", "run"]  # claim strictly before side effect

    def test_refused_claim_skips_run_job(self):
        with patch("cron.scheduler.get_due_jobs", return_value=[self._oneshot()]), \
             patch("cron.scheduler.claim_job_for_fire", return_value=True), \
             patch("cron.scheduler.claim_dispatch", return_value=False), \
             patch("cron.scheduler.run_job") as run_mock, \
             patch("cron.scheduler.save_job_output"), \
             patch("cron.scheduler.mark_job_run") as mark_mock:
            from cron.scheduler import tick
            tick(verbose=False)
        run_mock.assert_not_called()
        mark_mock.assert_not_called()

class TestParseWakeGate:
    """Unit tests for _parse_wake_gate — pure function, no side effects."""

    def test_empty_output_wakes(self):
        from cron.scheduler import _parse_wake_gate
        assert _parse_wake_gate("") is True
        assert _parse_wake_gate(None) is True

    def test_wake_gate_false_skips(self):
        from cron.scheduler import _parse_wake_gate
        assert _parse_wake_gate('{"wakeAgent": false}') is False

class TestRunJobWakeGate:
    """Integration tests for run_job wake-gate short-circuit."""

    @pytest.fixture(autouse=True)
    def _stub_runtime_provider(self):
        """Stub ``resolve_runtime_provider`` for wake-gate tests.

        ``run_job`` resolves the runtime provider BEFORE constructing
        ``AIAgent``, so these tests must mock ``resolve_runtime_provider``
        in addition to ``AIAgent`` — otherwise in a hermetic CI env (no
        API keys), the resolver raises and the test fails before the
        patched AIAgent is ever reached.
        """
        fake_runtime = {
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "test-key",
            "source": "stub",
            "requested_provider": None,
        }
        with patch(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            return_value=fake_runtime,
        ):
            yield

    def _make_job(self, name="wake-gate-test", script="check.py"):
        """Minimal valid cron job dict for run_job."""
        return {
            "id": f"job_{name}",
            "name": name,
            "prompt": "Do a thing",
            "schedule": "*/5 * * * *",
            "script": script,
        }

    def test_wake_false_skips_agent_and_returns_silent(self, caplog, tmp_path):
        """When _run_job_script output ends with {wakeAgent: false}, the agent
        is not invoked and run_job returns the SILENT marker so delivery is
        suppressed."""
        from cron import scheduler
        from cron import scheduler_script as sched_script

        with patch.object(sched_script, "_run_job_script",
                          return_value=(True, '{"wakeAgent": false}')), \
             patch("run_agent.AIAgent") as agent_cls:
            success, _doc, final, err = _run_owned_job(self._make_job(), tmp_path)

        assert success is True
        assert err is None
        assert final == SILENT_MARKER
        agent_cls.assert_not_called()

    def test_wake_true_runs_agent_with_injected_output(self, tmp_path):
        """When the script returns {wakeAgent: true, data: ...}, the agent is
        invoked and the data line still shows up in the prompt."""
        from cron import scheduler
        from cron import scheduler_script as sched_script

        script_output = '{"wakeAgent": true, "data": {"new": 3}}'
        agent = MagicMock()
        agent.run_conversation = MagicMock(return_value={
            "final_response": "ok", "messages": []
        })
        with patch.object(sched_script, "_run_job_script",
                          return_value=(True, script_output)), \
             patch("run_agent.AIAgent", return_value=agent) as agent_cls:
            success, _doc, _final, err = _run_owned_job(self._make_job(), tmp_path)

        agent_cls.assert_called_once()
        # The script output should be visible in the prompt passed to
        # run_conversation.
        call_kwargs = agent.run_conversation.call_args
        prompt_arg = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("user_message", "")
        assert script_output in prompt_arg
        assert success is True
        assert err is None

class TestBuildJobPromptMissingSkill:
    """Verify that a missing skill logs a warning and does not crash the job."""

    def _missing_skill_view(self, name: str) -> str:
        return json.dumps({"success": False, "error": f"Skill '{name}' not found."})

    def test_missing_skill_injects_user_notice_into_prompt(self):
        """A system notice about the missing skill is injected into the prompt."""
        with patch("tools.skills_tool.skill_view", side_effect=self._missing_skill_view):
            result = _build_job_prompt({"skills": ["ghost-skill"], "prompt": "do something"})
        assert "ghost-skill" in result
        assert "not found" in result.lower() or "skipped" in result.lower()

class TestBuildJobPromptAbsoluteSkillPath:
    """Cron jobs may store absolute skill paths; normalize before skill_view."""

    def test_absolute_skill_path_normalized_before_skill_view(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "alpha-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Alpha\nDo alpha.")
        absolute_path = str(skill_dir)
        seen_names: list[str] = []

        def _skill_view(name: str) -> str:
            seen_names.append(name)
            if name == "alpha-skill":
                return json.dumps({"success": True, "content": "# Alpha\nDo alpha."})
            return json.dumps({"success": False, "error": f"Skill '{name}' not found."})

        with patch("tools.skills_tool.SKILLS_DIR", skills_dir), \
             patch("tools.skills_tool.skill_view", side_effect=_skill_view):
            result = _build_job_prompt({"skills": [absolute_path], "prompt": "go"})

        assert seen_names == ["alpha-skill"]
        assert "Do alpha." in result

class TestBuildJobPromptBumpUse:
    """Verify that cron jobs bump skill usage counters so the curator sees them as active."""

    def test_bump_use_called_for_loaded_skill(self):
        """bump_use is called for each successfully loaded skill."""

        def _skill_view(name: str) -> str:
            return json.dumps({"success": True, "content": f"Content for {name}."})

        with patch("tools.skills_tool.skill_view", side_effect=_skill_view), \
             patch("tools.skill_usage.bump_use") as mock_bump:
            _build_job_prompt({
                "id": "cron-task",
                "skills": ["alpha", "beta"],
                "prompt": "go",
            })

        assert mock_bump.call_count == 2
        calls = [c[0][0] for c in mock_bump.call_args_list]
        assert "alpha" in calls
        assert "beta" in calls
        assert all(
            call.kwargs == {"task_id": "cron-task"}
            for call in mock_bump.call_args_list
        )


class TestParallelTick:
    """Verify that tick() runs due jobs concurrently and isolates ContextVars."""

    @pytest.fixture(autouse=True)
    def _isolate_tick_lock(self, tmp_path):
        """Point the tick file lock at a per-test temp dir to avoid lock contention."""
        lock_dir = tmp_path / "cron"
        lock_dir.mkdir()
        lock_file = lock_dir / ".tick.lock"
        with patch("cron.scheduler._get_lock_paths", return_value=(lock_dir, lock_file)):
            yield

    def test_parallel_jobs_run_concurrently(self):
        """Two jobs launched in the same tick should overlap in time."""
        import threading

        barrier = threading.Barrier(2, timeout=5)
        call_order = []

        def mock_run_job(job, *, defer_agent_teardown=None, **kw):
            """Each job hits a barrier — both must be active simultaneously."""
            call_order.append(("start", job["id"]))
            barrier.wait()  # blocks until both threads reach here
            call_order.append(("end", job["id"]))
            return (True, "output", "response", None)

        jobs = [
            {"id": "job-a", "name": "a", "deliver": "local"},
            {"id": "job-b", "name": "b", "deliver": "local"},
        ]

        with patch("cron.scheduler.get_due_jobs", return_value=jobs), \
             patch("cron.scheduler.claim_job_for_fire", return_value=True), \
             patch("cron.scheduler.run_job", side_effect=mock_run_job), \
             patch("cron.scheduler.save_job_output", return_value="/tmp/out.md"), \
             patch("cron.scheduler.mark_job_run"):
            from cron.scheduler import tick
            result = tick(verbose=False)

        assert result == 2
        # Both starts happened before both ends — proof of concurrency
        starts = [i for i, (action, _) in enumerate(call_order) if action == "start"]
        ends = [i for i, (action, _) in enumerate(call_order) if action == "end"]
        assert len(starts) == 2
        assert len(ends) == 2
        assert max(starts) < min(ends), f"Jobs not concurrent: {call_order}"

    def test_max_parallel_env_var(self, monkeypatch):
        """HERMES_CRON_MAX_PARALLEL=1 should restore serial behaviour."""
        monkeypatch.setenv("HERMES_CRON_MAX_PARALLEL", "1")
        call_times = []

        def mock_run_job(job, *, defer_agent_teardown=None, **_kw):
            import time
            call_times.append(("start", job["id"], time.monotonic()))
            time.sleep(0.05)
            call_times.append(("end", job["id"], time.monotonic()))
            return (True, "output", "response", None)

        jobs = [
            {"id": "s1", "name": "s1", "deliver": "local"},
            {"id": "s2", "name": "s2", "deliver": "local"},
        ]

        with patch("cron.scheduler.get_due_jobs", return_value=jobs), \
             patch("cron.scheduler.claim_job_for_fire", return_value=True), \
             patch("cron.scheduler.run_job", side_effect=mock_run_job), \
             patch("cron.scheduler.save_job_output", return_value="/tmp/out.md"), \
             patch("cron.scheduler.mark_job_run"):
            from cron.scheduler import tick
            result = tick(verbose=False)

        assert result == 2
        # With max_workers=1, second job starts after first ends
        end_s1 = next(t for action, jid, t in call_times if action == "end" and jid == "s1")
        start_s2 = next(t for action, jid, t in call_times if action == "start" and jid == "s2")
        assert start_s2 >= end_s1, "Jobs ran concurrently despite max_parallel=1"











class TestBuildJobPromptExtraPrompt:
    """Regression: _build_job_prompt merges extra_prompt into the assembled prompt."""

    def test_extra_prompt_appended_with_header(self):
        """extra_prompt appears under a '## Run Context' header."""
        job = {"prompt": "stored prompt"}
        result = _build_job_prompt(job, extra_prompt="CONTEXT: client=Foo")
        assert "stored prompt" in result
        assert "CONTEXT: client=Foo" in result

    def test_extra_prompt_does_not_mutate_job(self):
        """The job dict's 'prompt' field must remain unchanged."""
        job = {"prompt": "original"}
        _build_job_prompt(job, extra_prompt="transient context")
        assert job["prompt"] == "original"
