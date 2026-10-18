"""Retired providers must not turn stale session URLs into custom proxies."""


def test_sealed_session_routes_drop_retired_endpoints_but_keep_custom_proxies_a_b_a(tmp_path, monkeypatch):
    import json

    from hermes_cli.cli_model_switch_mixin import stored_session_route
    from hermes_cli.config import save_config
    from hermes_security import vault
    from hermes_state import SessionDB
    from tui_runtime.server import _stored_session_runtime_overrides

    homes = [tmp_path / name for name in ("A", "B")]
    stale_urls = ("https://inference-api.nousresearch.com/v1", "https://openrouter.ai/api/v1")
    try:
        for home, url in zip(homes, stale_urls):
            monkeypatch.setenv("HERMES_HOME", str(home))
            vault.init_vault(home, "synthetic-restored-route-password")
            vault.unlock(home, "synthetic-restored-route-password")
            save_config({"providers": {"explicit-proxy": {"base_url": url}}})
            with SessionDB(db_path=home / "state.db") as db:
                db.create_session("stale", source="telegram", model="gpt-5.4")
                db.update_session_meta("stale", json.dumps({"provider": "openai-codex",
                    "base_url": url, "api_mode": "chat_completions"}), "gpt-5.4")
                db.create_session("proxy", source="cli", model="proxy-model")
                db.update_session_meta("proxy", json.dumps({"provider": "custom:explicit-proxy",
                    "base_url": url, "api_mode": "chat_completions"}), "proxy-model")
        for home in (homes[0], homes[1], homes[0]):
            monkeypatch.setenv("HERMES_HOME", str(home))
            with SessionDB(db_path=home / "state.db") as db:
                row = db.get_session("stale")
                desktop = _stored_session_runtime_overrides(row)["model_override"]
                assert (desktop["provider"], desktop["base_url"], desktop["api_mode"]) == (
                    "openai-codex", None, None)
                assert stored_session_route(row, current_model="other", current_provider="anthropic")[1:4] == (
                    "openai-codex", None, None)
                proxy = _stored_session_runtime_overrides(db.get_session("proxy"))["model_override"]
                assert proxy["provider"] == "custom:explicit-proxy" and proxy["base_url"] in stale_urls
            assert not (home / "state.db").read_bytes().startswith(b"SQLite format 3")
    finally:
        vault.clear_vault_cache()
