"""A reported plaintext database cannot be a healthy repair result."""

from argparse import Namespace
from pathlib import Path
import sqlite3

import pytest


@pytest.mark.platforms("posix")
def test_repair_reports_failure_when_a_plaintext_database_remains(tmp_path, monkeypatch, capsys):
    from hermes_cli.vault_cmd import cmd_vault
    from hermes_security import vault

    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    vault.init_vault(home, "synthetic-repair-password")
    vault.unlock(home, "synthetic-repair-password")
    path = home / "state.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE retained (value TEXT)")
        db.execute("INSERT INTO retained VALUES ('must-survive')")
    before = path.read_bytes()
    result = cmd_vault(Namespace(vault_command="repair"))
    assert result != 0
    assert "PLAINTEXT" in capsys.readouterr().out
    assert path.read_bytes() == before
    assert Path(path).is_file()


@pytest.mark.platforms("posix")
def test_offline_repair_refuses_a_live_profile_before_changing_any_state(tmp_path, monkeypatch, capsys):
    from hermes_cli.vault_cmd import cmd_vault
    from hermes_security import vault
    from hermes_state.runtime_ownership import ProfileOwnership

    home = (tmp_path / "home").resolve()
    monkeypatch.setenv("HERMES_HOME", str(home))
    vault.init_vault(home, "synthetic busy repair password")
    vault.unlock(home, "synthetic busy repair password")
    path = home / "SOUL.md"
    path.write_bytes(b"synthetic unsealed state that must remain unchanged while busy")
    owner = ProfileOwnership()
    owner.reserve([home])
    try:
        before = path.read_bytes()
        result = cmd_vault(Namespace(vault_command="repair"))
        assert result != 0 and path.read_bytes() == before
        assert "session to be closed" in capsys.readouterr().out
    finally:
        owner.close()
