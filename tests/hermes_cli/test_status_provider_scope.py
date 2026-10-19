"""Status describes supported inference without activating retired credentials."""

from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("deep", [False, True], ids=["full", "deep"])
def test_status_ignores_retired_provider_credentials(monkeypatch, capsys, tmp_path, deep):
    from hermes_cli import auth, status
    import httpx

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setenv("OPENROUTER_API_KEY", "retired-provider-sentinel")
    monkeypatch.setenv("TAVILY_API_KEY", "tool-key-sentinel-value")
    monkeypatch.setenv("LITELLM_API_KEY", "supported-gateway-sentinel")
    monkeypatch.setattr(auth, "get_nous_auth_status_local", dict)
    monkeypatch.setattr(auth, "get_codex_auth_status", dict)
    monkeypatch.setattr(auth, "get_xai_oauth_auth_status", dict)

    def retired_probe(*args, **kwargs):
        pytest.fail("status activated a retired inference provider")

    monkeypatch.setattr(auth, "get_qwen_auth_status", retired_probe)
    monkeypatch.setattr(auth, "get_minimax_oauth_auth_status", retired_probe)
    monkeypatch.setattr(httpx, "get", retired_probe)

    status.show_status(SimpleNamespace(full=True, deep=deep))

    output = capsys.readouterr().out
    assert "LiteLLM gateway" in output
    assert "Tavily" in output
    assert "Qwen OAuth" not in output
    assert "MiniMax OAuth" not in output
    assert "OpenRouter" not in output
    assert "retired-provider-sentinel" not in output
    assert "tool-key-sentinel-value" not in output
    assert "supported-gateway-sentinel" not in output
