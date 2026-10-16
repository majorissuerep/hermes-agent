"""A snapshot made before encryption must still restore after vault migration."""
from pathlib import Path

import pytest

pytestmark = pytest.mark.platforms("posix")


def test_pre_vault_snapshot_keeps_config_credentials_and_conversation_restorable(tmp_path, monkeypatch):
    from hermes_cli.backup import create_quick_snapshot, restore_quick_snapshot
    from hermes_cli.config import save_config, save_env_value, load_env, read_raw_config
    from hermes_security import migrate
    from hermes_state import SessionDB

    home, user = tmp_path / "state", tmp_path / "user"
    home.mkdir()
    user.mkdir()
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setattr(Path, "home", lambda: user)
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / "config.yaml").write_text("# legacy comment\nagent:\n  max_turns: 37\n")
    (home / ".env").write_text("LEGACY_API_KEY=synthetic-legacy-secret\n")
    with SessionDB(home / "state.db") as database:
        database.create_session("legacy", "cli")
        database.append_message("legacy", "user", "original retained conversation")
    snapshot = create_quick_snapshot(label="before-vault", hermes_home=home)
    assert snapshot
    assert migrate.migrate_home(home, "synthetic-snapshot-password").ok
    saved = home / "state-snapshots" / snapshot
    original = {p.relative_to(saved): p.read_bytes() for p in saved.rglob("*") if p.is_file()}
    save_config({"agent": {"max_turns": 5}}, merge_existing=True)
    save_env_value("LEGACY_API_KEY", "deliberate-mutation")
    assert restore_quick_snapshot(snapshot, hermes_home=home)
    assert read_raw_config()["agent"]["max_turns"] == 37
    assert load_env()["LEGACY_API_KEY"] == "synthetic-legacy-secret"
    with SessionDB(home / "state.db") as database:
        assert database.get_messages("legacy")[0]["content"] == "original retained conversation"
        assert database._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert {p.relative_to(saved): p.read_bytes() for p in saved.rglob("*") if p.is_file()} == original
