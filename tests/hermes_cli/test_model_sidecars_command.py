"""The real CLI persists sidecar options through an encrypted profile home."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture
def vaulted_cli(tmp_path, monkeypatch):
    from hermes_security.vault import init_vault, unlock, generate_keypair, add_key_slot
    home = tmp_path / "hermes"
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_ALLOW_NO_VAULT", raising=False)
    init_vault(home, "sidecar-command-test")
    vault = unlock(home, "sidecar-command-test")
    private, public = generate_keypair()
    add_key_slot(home, public_key_raw=public, unlocked_vault=vault)
    key = tmp_path / "test-private-key"
    key.write_text(private.hex())
    key.chmod(0o600)
    monkeypatch.setenv("HERMES_VAULT_PRIVATE_KEY", str(key))

    def run(*args):
        return subprocess.run([sys.executable, "-m", "hermes_cli.main", "model", "sidecars", *args],
                              capture_output=True, text=True, timeout=30)
    return home, run


def test_cli_pool_and_independent_toggles_reach_the_profile_loader(vaulted_cli):
    from hermes_cli.config import load_config_readonly, save_config
    home, run = vaulted_cli
    cfg = load_config_readonly()
    cfg.setdefault("auxiliary", {})["tool_guard"] = {"provider": "custom", "model": "local-guard", "base_url": "http://127.0.0.1:8081/v1"}
    cfg["auxiliary"]["model_router"] = {"provider": "custom", "model": "local-router", "base_url": "http://127.0.0.1:8082/v1"}
    save_config(cfg)
    missing_pool = run("enable", "--role", "router")
    assert missing_pool.returncode == 1 and "pool-add" in missing_pool.stdout
    add = run("pool-add", "fast", "--provider", "custom", "--model", "fast-model", "--tier", "1",
              "--context-length", "32768", "--input-price", "0.2", "--output-price", "0.4")
    assert add.returncode == 0, add.stderr
    assert run("enable", "--role", "guard").returncode == 0
    cfg = load_config_readonly()
    assert cfg["approvals"]["guard"]["enabled"] is True
    assert cfg["smart_model_routing"]["enabled"] is False
    assert run("enable", "--role", "router").returncode == 0
    assert run("disable", "--role", "guard").returncode == 0
    cfg = load_config_readonly()
    assert cfg["approvals"]["guard"]["enabled"] is False
    assert cfg["smart_model_routing"]["enabled"] is True
    row = cfg["smart_model_routing"]["models"][0]
    assert row["model"] == "fast-model" and row["prices"]["output"] == 2 * row["prices"]["input"]
    # This tests encrypted write/read behavior, not the layout of a source file.
    assert b"fast-model" not in (home / "config.yaml").read_bytes()


def test_cli_catalog_pins_sources_and_rejects_invalid_prices_without_writing(vaulted_cli):
    from hermes_cli.config import load_config_readonly
    _home, run = vaulted_cli
    listing = run("list")
    assert listing.returncode == 0, listing.stderr
    catalog = json.loads(listing.stdout)
    assert catalog and all({"huggingface", "modelscope"} <= model["sources"].keys() for model in catalog)
    bad = run("pool-add", "invalid", "--provider", "custom", "--model", "fast", "--tier", "1",
              "--context-length", "32768", "--input-price", "-1", "--output-price", "0.4")
    assert bad.returncode == 1
    assert not load_config_readonly()["smart_model_routing"]["models"]
