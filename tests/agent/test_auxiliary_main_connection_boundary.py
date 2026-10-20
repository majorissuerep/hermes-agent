"""Real route selection with only the final SDK constructor replaced (no network)."""
from types import SimpleNamespace

import pytest

from agent import auxiliary_client as aux


@pytest.fixture
def constructed(monkeypatch):
    from hermes_cli import auth
    clients = []
    def construct(**kwargs):
        clients.append(kwargs)
        return SimpleNamespace(**kwargs)
    monkeypatch.setattr(aux, "_create_openai_client", construct)
    monkeypatch.setattr(auth, "resolve_api_key_provider_credentials", lambda provider: {
        "api_key": "stored-provider-key", "base_url": "https://api.z.ai/api/paas/v4"})
    monkeypatch.setattr(aux, "_read_main_provider", lambda: "zai")
    return clients


def test_explicit_vision_main_does_not_select_vendor_endpoint(constructed):
    runtime = {"provider": "zai", "model": "deployed-vision", "base_url": "https://working.example/v1",
               "api_key": "active-key", "api_mode": "chat_completions"}
    provider, client, model = aux.resolve_vision_provider_client("main", main_runtime=runtime)
    assert (provider, str(client.base_url), client.api_key, model) == (
        "zai", runtime["base_url"], "active-key", "deployed-vision")
    assert [entry["base_url"] for entry in constructed] == [runtime["base_url"]]


def test_foreign_main_endpoint_without_explicit_credential_is_refused(constructed):
    runtime = {"provider": "zai", "model": "deployed", "base_url": "https://working.example/v1", "api_key": "active-key"}
    with pytest.raises(aux.AuxiliaryClientUnavailable, match="explicit credential"):
        aux.resolve_provider_client("main", main_runtime=runtime, explicit_base_url="https://foreign.example/v1")
    assert constructed == []


def test_explicit_task_endpoint_key_and_mode_do_not_copy_named_route_headers(constructed, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text('providers:\n  relay:\n    base_url: https://working.example/v1\n'
        '    api_key: stored-provider-key\n    extra_headers:\n      Authorization: Bearer-old-header\n'
        'model:\n  default_headers:\n    Proxy-Authorization: main-proxy-secret\n'
        '  extra_headers:\n    X-Api-Key: main-header-secret\n')
    runtime = {"provider": "custom:relay", "model": "deployed", "base_url": "https://working.example/v1", "api_key": "active-key"}
    client, model = aux.resolve_provider_client("main", model="task-model", main_runtime=runtime,
        explicit_base_url="https://foreign.example/v1", explicit_api_key="task-key", api_mode="chat_completions")
    assert (str(client.base_url), client.api_key, model) == ("https://foreign.example/v1", "task-key", "task-model")
    assert "Authorization" not in constructed[-1].get("default_headers", {})
    assert "Proxy-Authorization" not in constructed[-1].get("default_headers", {})
    assert "X-Api-Key" not in constructed[-1].get("default_headers", {})
    aux.resolve_provider_client("main", main_runtime=runtime)
    assert constructed[-1]["default_headers"]["X-Api-Key"] == "main-header-secret"
