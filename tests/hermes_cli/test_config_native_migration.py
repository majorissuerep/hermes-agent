"""Old config stamps cannot invoke retired interface migrations or rewrite user prose."""

from pathlib import Path


def test_versioned_config_migrates_without_retired_roster_writer(tmp_path, monkeypatch):
    from hermes_cli.config import DEFAULT_CONFIG, migrate_config, read_raw_config

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / "config.yaml").write_text("_config_version: 40\ncron:\n  model_drift_guard: true\n")
    soul = "# My agent\n\n## Messaging other agents\nMy own notes.\n\n## Preferences\nKeep.\n"
    (home / "SOUL.md").write_text(soul)

    result = migrate_config(interactive=False, quiet=True)

    assert not result["warnings"], result["warnings"]
    config = read_raw_config()
    assert config["_config_version"] == DEFAULT_CONFIG["_config_version"]
    assert "model_drift_guard" not in config.get("cron", {})
    assert (home / "SOUL.md").read_text() == soul
