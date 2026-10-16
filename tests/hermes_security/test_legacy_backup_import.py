"""Full backups created before encryption remain usable in the same encrypted home."""
from argparse import Namespace
from pathlib import Path

import pytest

pytestmark = pytest.mark.platforms("posix")


@pytest.mark.parametrize("storage", ["external", "inside-home"])
@pytest.mark.parametrize("encrypted_backup", [False, True], ids=["pre-vault", "encrypted"])
def test_zip_restores_settings_secret_and_database_without_disabling_encryption(tmp_path, monkeypatch, storage, encrypted_backup):
    from hermes_cli import backup
    from hermes_cli.config import load_env, read_raw_config, save_config, save_env_value
    from hermes_security import migrate
    from hermes_state import SessionDB

    home, user = tmp_path / "state", tmp_path / "user"
    home.mkdir()
    user.mkdir()
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setattr(Path, "home", lambda: user)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(backup, "_revive_gateway_after_import", lambda *args: None)
    (home / "config.yaml").write_text("agent:\n  max_turns: 37\n")
    (home / ".env").write_text("LEGACY_IMPORT_API_KEY=synthetic-legacy-secret\n")
    with SessionDB(home / "state.db") as database:
        database.create_session("legacy", "cli")
        database.append_message("legacy", "user", "retained legacy backup conversation")
    (home / "logs").mkdir(exist_ok=True)
    (home / "logs" / "retained.log").write_text("retained original log\n")
    if encrypted_backup:
        assert migrate.migrate_home(home, "synthetic-import-password").ok
    archive = (home / "backups" if storage == "inside-home" else tmp_path) / "retained.zip"
    archive.parent.mkdir(exist_ok=True)
    backup.run_backup(Namespace(output=str(archive)))
    assert archive.is_file()
    if not encrypted_backup:
        assert migrate.migrate_home(home, "synthetic-import-password").ok
    original = archive.read_bytes()
    save_config({"agent": {"max_turns": 3}}, merge_existing=True)
    save_env_value("LEGACY_IMPORT_API_KEY", "deliberate-mutation")
    with SessionDB(home / "state.db") as database:
        database.append_message("legacy", "assistant", "deliberate later state")
    assert backup.run_import(Namespace(zipfile=str(archive), force=True)) in (None, 0)
    assert read_raw_config()["agent"]["max_turns"] == 37
    assert load_env()["LEGACY_IMPORT_API_KEY"] == "synthetic-legacy-secret"
    with SessionDB(home / "state.db") as database:
        assert [row["content"] for row in database.get_messages("legacy")] == ["retained legacy backup conversation"]
        assert database._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert (home / "config.yaml").read_bytes().startswith(b"HRMVAULT\0")
    assert not (home / "state.db").read_bytes().startswith(b"SQLite format 3")
    assert archive.read_bytes() == original
    from hermes_security.frames import read_frames

    assert list(read_frames(home / "logs" / "retained.log", purpose="log")) == [b"retained original log"]


def test_foreign_vault_metadata_is_refused_before_any_file_is_replaced(tmp_path, monkeypatch):
    import zipfile
    from hermes_cli.backup import run_import
    from hermes_security import io, vault

    home = tmp_path / "state"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    vault.init_vault(home, "synthetic-original-password")
    vault.unlock(home, "synthetic-original-password")
    io.write_text(home / "config.yaml", "agent:\n  max_turns: 37\n", purpose="config")
    before = {name: (home / name).read_bytes() for name in ("config.yaml", ".hermes-vault")}
    archive = tmp_path / "foreign.zip"
    with zipfile.ZipFile(archive, "w") as packed:
        packed.writestr("config.yaml", "agent:\n  max_turns: 3\n")
        packed.writestr(".hermes-vault", "foreign master-key metadata")
    with pytest.raises(SystemExit, match="vault metadata differs"):
        run_import(Namespace(zipfile=str(archive), force=True))
    assert {name: (home / name).read_bytes() for name in before} == before
