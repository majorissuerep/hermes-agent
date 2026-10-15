"""Tests for API-key provider support (z.ai/GLM, Kimi, MiniMax, AI Gateway)."""

import json as json_module
import os

import pytest

from hermes_cli.auth import (
    PROVIDER_REGISTRY,
    resolve_provider,
    get_api_key_provider_status,
    resolve_api_key_provider_credentials,
    AuthError,
    KIMI_CODE_BASE_URL,
    STEPFUN_STEP_PLAN_INTL_BASE_URL,
    _resolve_kimi_base_url,
)
from hermes_cli.copilot_auth import _try_gh_cli_token


# =============================================================================
# Provider Registry tests
# =============================================================================


# =============================================================================
# Provider Resolution tests
# =============================================================================

# Derived from the live PROVIDER_REGISTRY so the list can never drift when a
# new provider (and its env var) is added — a hand-maintained tuple here was
# missing HF_TOKEN/DEEPINFRA_API_KEY, which made the auto-detection tests
# env-dependent (they failed on any machine with HF_TOKEN exported).
from hermes_cli.auth import PROVIDER_REGISTRY as _REGISTRY

_EXTRA_ENV_VARS = (
    # Checked directly in resolve_provider("auto"), not via the registry.
    "AI_GATEWAY_API_KEY", "NOUS_API_KEY",
    # Base URLs / paths that influence detection but aren't api_key_env_vars.
    "LM_BASE_URL", "KIMI_BASE_URL", "STEPFUN_BASE_URL", "KILOCODE_BASE_URL",
    "GMI_BASE_URL", "OPENAI_BASE_URL",
    "HERMES_COPILOT_ACP_COMMAND", "COPILOT_CLI_PATH",
    "HERMES_COPILOT_ACP_ARGS", "COPILOT_ACP_BASE_URL",
)

PROVIDER_ENV_VARS = tuple(
    dict.fromkeys(
        [var for cfg in _REGISTRY.values() for var in cfg.api_key_env_vars]
        + list(_EXTRA_ENV_VARS)
    )
)


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch):
    for key in PROVIDER_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("hermes_cli.auth._load_auth_store", dict)


class TestResolveProvider:
    """Test resolve_provider() with new providers."""


    def test_alias_zhipu(self):
        assert resolve_provider("zhipu") == "zai"

    def test_alias_vercel(self):
        assert resolve_provider("vercel") == "ai-gateway"


    def test_alias_case_insensitive(self):
        assert resolve_provider("GLM") == "zai"
        assert resolve_provider("Z-AI") == "zai"

    def test_alias_chatgpt(self):
        """Issue #95794: ``--provider chatgpt`` selects the ChatGPT-backed Codex OAuth provider."""
        assert resolve_provider("chatgpt") == "openai-codex"
        assert resolve_provider("chatgpt-codex") == "openai-codex"

    def test_alias_chatgpt_every_alias_table(self):
        """Issue #95794: the runtime (providers.py), the /model parser (models_catalog_static via
        parse_model_input) and ``hermes auth login`` all resolve the ChatGPT alias, not just auth."""
        from hermes_cli.providers import normalize_provider
        from hermes_cli.models import parse_model_input
        from hermes_cli.auth_commands import _normalize_provider

        assert normalize_provider("chatgpt") == "openai-codex"
        assert normalize_provider("chatgpt-codex") == "openai-codex"
        assert parse_model_input("chatgpt:gpt-5.5", "ai-gateway") == ("openai-codex", "gpt-5.5")
        assert parse_model_input("chatgpt-codex:gpt-5.5", "ai-gateway") == ("openai-codex", "gpt-5.5")
        assert _normalize_provider("chatgpt") == "openai-codex"

    def test_unknown_provider_raises(self):
        with pytest.raises(AuthError):
            resolve_provider("nonexistent-provider-xyz")

    def test_auto_detects_glm_key(self, monkeypatch):
        monkeypatch.setenv("GLM_API_KEY", "test-glm-key")
        assert resolve_provider("auto") == "zai"


    def test_auto_does_not_select_copilot_from_github_token(self, monkeypatch):
        # AWS Bedrock auto-detection (via boto3's credential chain) runs at
        # the tail of resolve_provider("auto") and will silently pick up
        # ~/.aws/credentials on developer machines that aren't blanked by
        # the hermetic conftest. Force-disable it so this test exercises
        # the specific "GitHub token alone shouldn't auto-pick copilot"
        # behavior, not the Bedrock fallback.
        monkeypatch.setattr(
            "agent.bedrock_adapter.has_aws_credentials",
            lambda env=None, **_kw: False,
        )
        monkeypatch.setenv("GITHUB_TOKEN", "gh-test-token")
        with pytest.raises(AuthError, match="not connected to any AI provider"):
            resolve_provider("auto")


# =============================================================================
# API Key Provider Status tests
# =============================================================================

class TestApiKeyProviderStatus:

    def test_unconfigured_provider(self):
        status = get_api_key_provider_status("zai")
        assert status["configured"] is False
        assert status["logged_in"] is False

    def test_configured_provider(self, monkeypatch):
        monkeypatch.setenv("GLM_API_KEY", "test-key-123")
        status = get_api_key_provider_status("zai")
        assert status["configured"] is True
        assert status["logged_in"] is True
        assert status["key_source"] == "GLM_API_KEY"
        assert "z.ai" in status["base_url"].lower() or "api.z.ai" in status["base_url"]


# =============================================================================
# Credential Resolution tests
# =============================================================================

# =============================================================================
# Runtime Provider Resolution tests
# =============================================================================

class TestRuntimeProviderResolution:

    def test_runtime_zai(self, monkeypatch):
        monkeypatch.setenv("GLM_API_KEY", "glm-key")
        from hermes_cli.runtime_provider import resolve_runtime_provider
        result = resolve_runtime_provider(requested="zai")
        assert result["provider"] == "zai"
        assert result["api_mode"] == "chat_completions"
        assert result["api_key"] == "glm-key"
        assert "z.ai" in result["base_url"] or "api.z.ai" in result["base_url"]


    def test_runtime_auto_detects_api_key_provider(self, monkeypatch):
        monkeypatch.setenv("GLM_API_KEY", "auto-zai-key")
        from hermes_cli.runtime_provider import resolve_runtime_provider
        result = resolve_runtime_provider(requested="auto")
        assert result["provider"] == "zai"
        assert result["api_key"] == "auto-zai-key"

# =============================================================================
# _has_any_provider_configured tests
# =============================================================================

class TestHasAnyProviderConfigured:


    def test_claude_code_creds_ignored_on_fresh_install(self, monkeypatch, tmp_path):
        """Claude Code credentials should NOT skip the wizard when Hermes is unconfigured."""
        from hermes_cli import config as config_module
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        monkeypatch.setattr(config_module, "get_env_path", lambda: hermes_home / ".env")
        monkeypatch.setattr(config_module, "get_hermes_home", lambda: hermes_home)
        monkeypatch.setattr("hermes_cli.copilot_auth.resolve_copilot_token", lambda: ("", ""))
        # Clear all provider env vars so earlier checks don't short-circuit
        _all_vars = {"AI_GATEWAY_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                      "ANTHROPIC_TOKEN", "OPENAI_BASE_URL"}
        for pconfig in PROVIDER_REGISTRY.values():
            if pconfig.auth_type == "api_key":
                _all_vars.update(pconfig.api_key_env_vars)
        for var in _all_vars:
            monkeypatch.delenv(var, raising=False)
        # Prevent gh-cli / copilot auth fallback from leaking in
        monkeypatch.setattr("hermes_cli.auth.get_auth_status", lambda _pid: {})
        # Simulate valid Claude Code credentials
        monkeypatch.setattr(
            "agent.anthropic_credentials.read_claude_code_credentials",
            lambda: {"accessToken": "sk-ant-test", "refreshToken": "ref-tok"},
        )
        monkeypatch.setattr(
            "agent.anthropic_credentials.is_claude_code_token_valid",
            lambda creds: True,
        )
        from hermes_cli.main import _has_any_provider_configured
        assert _has_any_provider_configured() is False

    def test_config_provider_counts(self, monkeypatch, tmp_path):
        """config.yaml with model.provider set should count as configured."""
        import hermes_yaml as yaml
        from hermes_cli import config as config_module
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        config_file = hermes_home / "config.yaml"
        config_file.write_text(yaml.safe_dump({
            "model": {"default": "anthropic/claude-opus-4.6", "provider": "ai-gateway"},
        }))
        monkeypatch.setattr(config_module, "get_env_path", lambda: hermes_home / ".env")
        monkeypatch.setattr(config_module, "get_hermes_home", lambda: hermes_home)
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        # Clear all provider env vars
        for var in ("AI_GATEWAY_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                     "ANTHROPIC_TOKEN", "OPENAI_BASE_URL"):
            monkeypatch.delenv(var, raising=False)
        from hermes_cli.main import _has_any_provider_configured
        assert _has_any_provider_configured() is True

    @staticmethod
    def _clear_provider_env(monkeypatch):
        """Clear every provider env var so early checks can't short-circuit."""
        _all_vars = {"AI_GATEWAY_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                     "ANTHROPIC_TOKEN", "OPENAI_BASE_URL"}
        for pconfig in PROVIDER_REGISTRY.values():
            if pconfig.auth_type == "api_key":
                _all_vars.update(pconfig.api_key_env_vars)
        for var in _all_vars:
            monkeypatch.delenv(var, raising=False)

    def _setup_home(self, monkeypatch, tmp_path):
        from hermes_cli import config as config_module
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        monkeypatch.setattr(config_module, "get_env_path", lambda: hermes_home / ".env")
        monkeypatch.setattr(config_module, "get_hermes_home", lambda: hermes_home)
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        self._clear_provider_env(monkeypatch)
        return hermes_home

    def test_config_provider_skips_registry_sweep(self, monkeypatch, tmp_path):
        """model.provider in config.yaml must short-circuit BEFORE the slow
        provider-registry sweep (gh subprocess etc.) is ever invoked.

        Regression test for the auth-first ordering: get_auth_status is
        booby-trapped to fail loudly if the sweep runs. The sweep wraps its
        loop in ``except Exception``, so we also record every call — any
        recorded call proves the sweep ran even if the raise was swallowed.
        """
        import hermes_yaml as yaml
        hermes_home = self._setup_home(monkeypatch, tmp_path)
        (hermes_home / "config.yaml").write_text(yaml.safe_dump({
            "model": {"default": "anthropic/claude-opus-4.6", "provider": "ai-gateway"},
        }))
        sweep_calls = []

        def _trap(provider_id):
            sweep_calls.append(provider_id)
            raise AssertionError("sweep must be skipped")

        monkeypatch.setattr("hermes_cli.auth.get_auth_status", _trap)
        from hermes_cli.main import _has_any_provider_configured
        assert _has_any_provider_configured() is True
        assert sweep_calls == [], (
            f"provider registry sweep ran before config short-circuit: {sweep_calls}"
        )

    def test_config_base_url_api_key_skips_registry_sweep(self, monkeypatch, tmp_path):
        """Custom endpoint (base_url/api_key in config, no provider) must also
        short-circuit before the registry sweep."""
        import hermes_yaml as yaml
        hermes_home = self._setup_home(monkeypatch, tmp_path)
        (hermes_home / "config.yaml").write_text(yaml.safe_dump({
            "model": {
                "default": "local/custom-model",
                "base_url": "http://localhost:8000/v1",
                "api_key": "sk-local-test",
            },
        }))
        sweep_calls = []

        def _trap(provider_id):
            sweep_calls.append(provider_id)
            raise AssertionError("sweep must be skipped")

        monkeypatch.setattr("hermes_cli.auth.get_auth_status", _trap)
        from hermes_cli.main import _has_any_provider_configured
        assert _has_any_provider_configured() is True
        assert sweep_calls == [], (
            f"provider registry sweep ran before config short-circuit: {sweep_calls}"
        )

    def test_auth_json_skips_registry_sweep(self, monkeypatch, tmp_path):
        """auth.json with a logged-in active provider must short-circuit before
        the registry sweep. get_auth_status may be called ONLY for the active
        provider from auth.json — any other provider id means the sweep ran.
        """
        hermes_home = self._setup_home(monkeypatch, tmp_path)
        (hermes_home / "auth.json").write_text(json_module.dumps({
            "active_provider": "openai-codex",
        }))
        calls = []

        def _guarded_status(provider_id):
            calls.append(provider_id)
            assert provider_id == "openai-codex", "sweep must be skipped"
            return {"logged_in": True}

        monkeypatch.setattr("hermes_cli.auth.get_auth_status", _guarded_status)
        from hermes_cli.main import _has_any_provider_configured
        assert _has_any_provider_configured() is True
        assert calls == ["openai-codex"], (
            f"provider registry sweep ran before auth.json short-circuit: {calls}"
        )


# =============================================================================
# Kimi Code auto-detection tests
# =============================================================================

MOONSHOT_DEFAULT_URL = "https://api.moonshot.ai/v1"


class TestZaiEndpointAutoDetect:
    """Test that resolve_api_key_provider_credentials auto-detects Z.AI endpoints."""


    def test_env_override_skips_probe(self, monkeypatch):
        """GLM_BASE_URL should always win without probing."""
        monkeypatch.setenv("GLM_API_KEY", "glm-key")
        monkeypatch.setenv("GLM_BASE_URL", "https://custom.example/v4")
        probe_called = False

        def _never_called(*a, **kw):
            nonlocal probe_called
            probe_called = True

        monkeypatch.setattr("hermes_cli.auth.detect_zai_endpoint", _never_called)
        creds = resolve_api_key_provider_credentials("zai")
        assert creds["base_url"] == "https://custom.example/v4"
        assert not probe_called

    def test_no_key_skips_probe(self, monkeypatch):
        """Without an API key, no probe should occur."""
        monkeypatch.setattr("hermes_cli.auth.detect_zai_endpoint", lambda *a, **kw: None)
        creds = resolve_api_key_provider_credentials("zai")
        assert creds["api_key"] == ""

    def test_failed_probe_is_not_repeated_within_ttl(self, monkeypatch):
        """A key whose detection fails (429 on every endpoint) is probed once, not on every
        credential resolution — the picker resolves Z.AI dozens of times per open (#114215)."""
        from hermes_cli import auth_zai_kimi
        monkeypatch.setenv("GLM_API_KEY", "glm-key-that-429s")
        monkeypatch.setattr(auth_zai_kimi, "_zai_probe_failed_until", {})
        calls = []
        monkeypatch.setattr("hermes_cli.auth.detect_zai_endpoint", lambda *a, **kw: calls.append(1))
        for _ in range(3):
            assert resolve_api_key_provider_credentials("zai")["base_url"] == "https://api.z.ai/api/paas/v4"
        assert len(calls) == 1


class TestZaiParallelProbe:
    """detect_zai_endpoint probes endpoints in parallel workers.

    Contract under test: (1) each endpoint worker preserves the per-endpoint
    candidate-model fallback loop, (2) when several endpoints succeed the
    winner is chosen by ZAI_ENDPOINTS priority order (not completion order).
    """

    def _mock_post(self, ok):
        """Return an httpx.post replacement; `ok` maps (base_url, model) -> bool."""
        import httpx as _httpx

        def _post(url, headers=None, json=None, timeout=None):
            base = url.rsplit("/chat/completions", 1)[0]
            code = 200 if ok.get((base, json["model"])) else 401
            request = _httpx.Request("POST", url)
            return _httpx.Response(code, request=request, json={})

        return _post

    def test_candidate_model_fallback_within_endpoint(self, monkeypatch):
        """A worker must try its endpoint's later candidate models when the
        first ones fail — the fallback the scalar-model version dropped."""
        from hermes_cli.auth import ZAI_ENDPOINTS, detect_zai_endpoint

        coding_global = next(ep for ep in ZAI_ENDPOINTS if ep[0] == "coding-global")
        base = coding_global[1]
        last_model = coding_global[2][-1]
        # Only the LAST candidate model of coding-global succeeds.
        monkeypatch.setattr(
            "hermes_cli.auth.httpx.post",
            self._mock_post({(base, last_model): True}),
        )
        result = detect_zai_endpoint("test-key", timeout=1.0)
        assert result is not None
        assert result["id"] == "coding-global"
        assert result["model"] == last_model

    def test_priority_order_wins_over_completion_order(self, monkeypatch):
        """When multiple endpoints accept the key, the FIRST in
        ZAI_ENDPOINTS order must win, even if another finishes earlier."""
        import time as _time

        from hermes_cli.auth import ZAI_ENDPOINTS, detect_zai_endpoint

        first = ZAI_ENDPOINTS[0]
        last = ZAI_ENDPOINTS[-1]
        ok = {
            (first[1], first[2][0]): True,
            (last[1], last[2][0]): True,
        }
        inner = self._mock_post(ok)

        def _slow_first(url, headers=None, json=None, timeout=None):
            if url.startswith(first[1]):
                _time.sleep(0.15)  # first-priority endpoint finishes LAST
            return inner(url, headers=headers, json=json, timeout=timeout)

        monkeypatch.setattr("hermes_cli.auth.httpx.post", _slow_first)
        result = detect_zai_endpoint("test-key", timeout=1.0)
        assert result is not None
        assert result["id"] == first[0]

    def test_all_fail_returns_none(self, monkeypatch):
        from hermes_cli.auth import detect_zai_endpoint

        monkeypatch.setattr("hermes_cli.auth.httpx.post", self._mock_post({}))
        assert detect_zai_endpoint("bad-key", timeout=1.0) is None

    def test_early_exit_does_not_wait_for_slow_losers(self, monkeypatch):
        """When the highest-priority endpoint succeeds fast, the caller must
        return without waiting for slow lower-priority probes to finish."""
        import time as _time

        from hermes_cli.auth import ZAI_ENDPOINTS, detect_zai_endpoint

        first = ZAI_ENDPOINTS[0]
        inner = self._mock_post({(first[1], first[2][0]): True})

        def _slow_losers(url, headers=None, json=None, timeout=None):
            if not url.startswith(first[1]):
                _time.sleep(2.0)  # slow lower-priority endpoints
            return inner(url, headers=headers, json=json, timeout=timeout)

        monkeypatch.setattr("hermes_cli.auth.httpx.post", _slow_losers)
        t0 = _time.perf_counter()
        result = detect_zai_endpoint("test-key", timeout=5.0)
        elapsed = _time.perf_counter() - t0
        assert result is not None and result["id"] == first[0]
        assert elapsed < 1.5, f"early exit failed: waited {elapsed:.2f}s for losers"


# =============================================================================
# Kimi / Moonshot model list isolation tests
# =============================================================================

# =============================================================================
# Hugging Face provider model list tests
# =============================================================================

# =============================================================================
# NovitaAI provider tests (added by feat/add-novita-provider)
# =============================================================================

# =============================================================================
# MiniMax OAuth provider tests (added by feat/minimax-oauth-provider)
# =============================================================================

# =============================================================================
# DeepInfra provider tests
# =============================================================================
# Registration / alias / env-var invariants are asserted in
# TestProviderRegistry + TestResolveProvider above. The classes below
# cover the catalog/tag/pricing/profile machinery added on top of the
# baseline provider wiring.


def _make_urlopen_returning(payload):
    """Helper: build a urlopen() shim returning a fixed JSON payload."""
    import json as _json

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _json.dumps(payload).encode()

    return lambda *a, **kw: _Resp()


@pytest.fixture
def _kilo_pricing_isolation(monkeypatch):
    """Reset the shared pricing caches around each Kilo pricing test.

    The Kilo fetcher keys the shared pricing cache by its catalog URL; without
    a reset, one test's payload (or its negative cache entry) would answer
    the next test's read.
    """
    import hermes_cli.models_pricing as _mp
    monkeypatch.setattr(_mp, "_pricing_cache", {})
    monkeypatch.setattr(_mp, "_pricing_cache_retry_after", {})
    monkeypatch.setattr(_mp, "_pricing_provider_cache_keys", {})
