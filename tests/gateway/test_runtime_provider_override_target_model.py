"""Gateway provider-override credential resolution keys off the override's MODEL (#112600).

Channel overrides, persisted ``/model`` switches and API-server provider refreshes all resolve
credentials through ``gateway.run._resolve_runtime_agent_kwargs_for_provider``; without the model
the ladder keys off config's ``default`` and a ``*-free`` default decides the api_mode/base_url
for a Go-only model ("Model ... is not supported")."""

import pytest


@pytest.fixture()
def _zen_free_default_home(monkeypatch, tmp_path):
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        "model:\n  default: claude-sonnet-4\n  provider: anthropic\n  base_url: https://api.anthropic.com\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("ZAI_API_KEY", "sk-test-go")


def test_provider_override_runtime_uses_the_override_model(_zen_free_default_home, monkeypatch):
    from gateway.run import _resolve_runtime_agent_kwargs_for_provider
    import hermes_cli.runtime_provider as rp

    real_resolve = rp.resolve_runtime_provider
    seen = []

    def resolve(**kwargs):
        seen.append(kwargs)
        return real_resolve(**kwargs)

    monkeypatch.setattr(rp, "resolve_runtime_provider", resolve)

    runtime = _resolve_runtime_agent_kwargs_for_provider("zai", target_model="glm-5")
    assert runtime["provider"] == "zai"
    assert runtime["base_url"] == "https://api.z.ai/api/paas/v4"
    assert seen[-1]["target_model"] == "glm-5"


def test_fallback_chain_runtime_uses_the_entry_model(_zen_free_default_home, monkeypatch):
    """The gateway's AuthError fallback goes through the shared ``resolve_runtime_with_fallback``
    walker (no gateway-private loop) and keeps the entry's own model."""
    import gateway.run as gateway_run
    import hermes_cli.runtime_provider as rp
    from hermes_cli.auth import AuthError

    real_resolve = rp.resolve_runtime_provider

    def primary_auth_fails(**kw):
        if kw.get("requested") is None:  # the primary, resolved from config.yaml
            raise AuthError("primary quota exhausted (429)")
        assert kw["target_model"] == "glm-5"
        return real_resolve(**kw)

    monkeypatch.setattr(rp, "resolve_runtime_provider", primary_auth_fails)
    monkeypatch.setattr(gateway_run, "_load_gateway_config",
                        lambda: {"fallback_model": [{"provider": "zai", "model": "glm-5"}]})
    fb = gateway_run._resolve_runtime_agent_kwargs()
    assert fb["model"] == "glm-5"
    assert fb["provider"] == "zai"
    assert fb["base_url"] == "https://api.z.ai/api/paas/v4"
