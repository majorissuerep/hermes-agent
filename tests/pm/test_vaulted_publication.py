"""The independent PM worker preserves encrypted profile config and receipts."""
import json

import pytest

from hermes_security import io, vault
from pm import receipt
from pm.plugin_inputs import Selection
from tests.pm.test_worker import client, isolated_python, _current_environment  # noqa: F401


@pytest.fixture
def vaulted_homes(client, isolated_python, tmp_path, monkeypatch):
    # stage_runtime builds the environment; prepare_runtime normally marks its role.
    (isolated_python.parent.parent / "pm-runtime.json").write_text("{}", encoding="utf-8")
    home = tmp_path / "home"
    sibling = home / "profiles" / "work"
    for target in (home, sibling):
        target.mkdir(parents=True, exist_ok=True)
        vault.init_vault(target, "pm-integration-password", _allow_existing_state=True)
        vault.unlock(target, "pm-integration-password")
        io.write_text(target / "config.yaml", '# preserve comment\nmodel: "private-canary"\n'
                      'plugins: {enabled: [plain]}\n', purpose="config")
        plugin = target / "plugins" / "plain"
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text("name: plain\n", encoding="utf-8")
    monkeypatch.delenv("HERMES_VAULT_PRIVATE_KEY", raising=False)
    try:
        yield home, sibling
    finally:
        vault.clear_vault_cache()


def test_worker_reads_profile_union_and_publishes_only_ciphertext(client, vaulted_homes, tmp_path, monkeypatch):
    from agent import secret_scope
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from pm.environments import install_state_dir
    from pm.plugins_state import enabled_plugins_ordered

    home, sibling = vaulted_homes
    project = _current_environment(tmp_path, monkeypatch, [])
    secret_scope.set_multiplex_active(True)
    try:
        for active in (home, sibling, home):
            home_token = set_hermes_home_override(active)
            secret_token = secret_scope.set_secret_scope({}, profile_home=str(active))
            try:
                assert client.venv_is_current()
                assert enabled_plugins_ordered() == {home / "plugins": ["plain"], sibling / "plugins": ["plain"]}
            finally:
                secret_scope.reset_secret_scope(secret_token)
                reset_hermes_home_override(home_token)
    finally:
        secret_scope.set_multiplex_active(False)
    previous_sibling = (sibling / "config.yaml").read_bytes()
    client.sync_venv(explicit=True, plugins=Selection({
        "home": str(home), "enabled": [], "disabled": ["plain"],
    }))
    text = io.read_text(home / "config.yaml", purpose="config")
    assert '# preserve comment' in text and 'model: "private-canary"' in text
    assert enabled_plugins_ordered() == {sibling / "plugins": ["plain"]}
    assert (sibling / "config.yaml").read_bytes() == previous_sibling
    assert receipt.latest()["outcome"] == "ok"
    assert not (install_state_dir(project) / "publication.json").exists()
    for path in [home / "config.yaml", *home.glob("logs/update_receipts/*.json")]:
        assert path.read_bytes().startswith(b"HRMVAULT\x00")
        assert b"private-canary" not in path.read_bytes()
    vault.lock_now(sibling)
    with pytest.raises(RuntimeError, match="locked"):
        client.venv_is_current()
    assert (sibling / "config.yaml").read_bytes() == previous_sibling


def test_pm_cli_reads_sealed_receipt_through_owner_without_worker_keys(client, vaulted_homes, tmp_path, monkeypatch, capsys):
    from pm.runtime import run_cli

    _current_environment(tmp_path, monkeypatch, [])
    token = receipt.begin("sync")
    receipt.finalize("ok", token=token)
    expected = receipt.latest()
    assert run_cli(["status"]) == 0
    assert json.loads(capsys.readouterr().out) == expected
