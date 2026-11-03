"""Model migration retains encrypted configuration and refuses stale or unreadable state."""
from types import SimpleNamespace

import pytest

import hermes_yaml as yaml
from hermes_cli.config_defaults import DEFAULT_CONFIG
from hermes_cli.xai_retirement import apply_migration, find_retired_xai_refs
from hermes_security import io, vault
from tests.e2e.vault_home import seal_fixture_home


def _profile(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir()
    path = home / "config.yaml"
    data = {"_config_version": DEFAULT_CONFIG["_config_version"],
            "model": {"provider": "openrouter", "default": "x-ai/grok-3"},
            "auxiliary": {"vision": {"provider": "xai", "model": "grok-3"}},
            "custom": {"migration_marker": "keep-me"}}
    original = "# operator routing\n" + yaml.safe_dump(data, sort_keys=False)
    path.write_text(original, encoding="utf-8")
    seal_fixture_home(home)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home, path, original, data


def test_public_migration_retains_ciphertext_comments_backup_and_provider_namespace(tmp_path, monkeypatch):
    from hermes_cli.migrate import cmd_migrate_xai

    home, path, original, _ = _profile(tmp_path, monkeypatch)
    assert cmd_migrate_xai(SimpleNamespace(apply=True, no_backup=False)) == 0
    assert path.read_bytes().startswith(b"HRMVAULT")
    text = io.read_text(path, purpose="config")
    changed = yaml.safe_load(text)
    assert text.startswith("# operator routing\n")
    assert changed["model"] == {"provider": "openrouter", "default": "x-ai/grok-4.3"}
    assert changed["auxiliary"]["vision"] == {"provider": "xai", "model": "grok-4.3"}
    assert changed["custom"] == {"migration_marker": "keep-me"}
    backups = list((home / "backups" / "config").glob("config.yaml.pre-migrate-xai.*"))
    assert len(backups) == 1
    assert backups[0].read_bytes().startswith(b"HRMVAULT")
    assert io.read_text(backups[0], purpose="config") == original


@pytest.mark.parametrize("state", ["locked", "damaged"])
def test_migration_refuses_unreadable_encrypted_config_without_changing_bytes(tmp_path, monkeypatch, state):
    home, path, _, data = _profile(tmp_path, monkeypatch)
    issues = find_retired_xai_refs(data)
    if state == "locked":
        vault.lock_now(home)
    else:
        damaged = bytearray(path.read_bytes())
        damaged[-1] ^= 1
        path.write_bytes(damaged)
    before = path.read_bytes()
    with pytest.raises(RuntimeError):
        apply_migration(path, issues)
    assert path.read_bytes() == before
    assert not list((home / "backups" / "config").glob("*"))


def test_migration_does_not_replace_a_model_changed_after_the_preview(tmp_path):
    path = tmp_path / "offline-config.yaml"
    data = {"auxiliary": {"vision": {"provider": "xai", "model": "grok-3"}}}
    issues = find_retired_xai_refs(data)
    data["auxiliary"]["vision"]["model"] = "operator-selected-model"
    original = yaml.safe_dump(data)
    path.write_text(original, encoding="utf-8")
    result = apply_migration(path, issues)
    assert not result.config_changed
    assert path.read_text(encoding="utf-8") == original
    assert result.backup_path is None
