"""An explicit main auxiliary route inherits the active connection, not provider defaults."""
import pytest

from agent import auxiliary_client as aux


@pytest.mark.parametrize("mode", ["chat_completions", "codex_responses", "anthropic_messages"])
def test_main_inherits_live_connection_and_preserves_explicit_task_override(monkeypatch, mode):
    runtime = {"provider": "litellm", "model": "deployed-main", "base_url": "https://active.example/v1",
               "api_key": "session-key", "api_mode": mode}
    monkeypatch.setattr(aux, "_read_main_provider", lambda: "litellm")
    monkeypatch.setattr(aux, "_read_main_model_for_aux", lambda: "stale-profile-model")
    monkeypatch.setattr(aux, "_resolve_registry_branch", lambda req: (req, req.model))
    monkeypatch.setitem(aux._EXPLICIT_PROVIDER_BRANCHES, "custom", lambda req: (req, req.model))
    client, model = aux.resolve_provider_client("main", main_runtime=runtime, task="title_generation")
    assert (client.explicit_base_url, client.explicit_api_key, client.api_mode, model) == (
        runtime["base_url"], "session-key", mode, "deployed-main")
    task, model = aux.resolve_provider_client("main", model="summary", main_runtime=runtime,
        explicit_base_url="https://task.example/v1", explicit_api_key="task-key", api_mode="chat_completions")
    assert (task.explicit_base_url, task.explicit_api_key, task.api_mode, model) == (
        "https://task.example/v1", "task-key", "chat_completions", "summary")
    with pytest.raises(aux.AuxiliaryClientUnavailable, match="explicit credential"):
        aux.resolve_provider_client("main", main_runtime=runtime, explicit_base_url="https://other.example/v1")


def test_cached_main_route_does_not_replace_the_active_key_with_another_pool_entry(monkeypatch):
    monkeypatch.setattr(aux, "_read_main_provider", lambda: "litellm")
    monkeypatch.setattr(aux, "_peek_pool_entry", lambda provider: object())
    monkeypatch.setattr(aux, "_pool_runtime_api_key", lambda entry: "another-pool-key")
    monkeypatch.setattr(aux, "_resolve_registry_branch", lambda req: (req, req.model))
    runtime = {"provider": "litellm", "model": "deployed-main", "base_url": "https://active.example/v1",
               "api_key": "active-session-key", "api_mode": "chat_completions"}
    with aux.aux_probe_mode():
        client, model = aux._get_cached_client("main", main_runtime=runtime, task="title_generation")
    assert client.explicit_api_key == "active-session-key" and model == "deployed-main"
