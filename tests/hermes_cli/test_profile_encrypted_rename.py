"""Renaming a native encrypted profile retains path-bound settings and durable history."""

from contextlib import closing
from pathlib import Path

import pytest


@pytest.mark.live_system_guard_bypass
def test_rename_rebinds_settings_and_database_without_changing_other_profile(tmp_path, monkeypatch):
    from hermes_cli import profiles
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from hermes_security import io, sqlite, vault
    from hermes_state import SessionDB
    from tests.e2e.vault_home import seal_fixture_home

    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text("model:\n  provider: litellm\n  default: fixture\n")
    (home / ".env").write_text("ACME_TOKEN=root-fixture\n")
    seal_fixture_home(home)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: home)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: home / "profiles")
    token = set_hermes_home_override(home)
    try:
        source = profiles.create_profile("old", no_skills=True, no_alias=True)
        io.write_text(source / ".env", "ACME_TOKEN=renamed-profile-private\n", purpose="env")
        with SessionDB(source / "state.db") as db:
            db.create_session("retained-history", source="cli")
            db.append_message("retained-history", "user", "immutable history prefix")
        machine_runtime = source / "installs/local-owner/environments/test-generation"
        machine_runtime.mkdir(parents=True)
        (machine_runtime / ".lease-managed").touch()
        before_root = (home / ".env").read_bytes()
        renamed = profiles.rename_profile("old", "new")
        assert not source.exists()
        assert io.read_text(renamed / "config.yaml", purpose="config")
        assert io.read_text(renamed / ".env", purpose="env") == "ACME_TOKEN=renamed-profile-private\n"
        with closing(sqlite.connect(renamed / "state.db")) as db:
            assert db.execute("SELECT content FROM messages").fetchall() == [("immutable history prefix",)]
        assert (home / ".env").read_bytes() == before_root
        assert not (renamed / ".hermes-vault").exists()
        assert not (renamed / "installs").exists()
        assert not list(renamed.parent.glob(".*.renaming-*"))
    finally:
        reset_hermes_home_override(token)
        vault.lock_now(home)


def test_failed_destination_publish_restores_original_authenticated_directory(tmp_path, monkeypatch):
    import os
    from hermes_cli.profile_state_copy import rename_profile_state
    from hermes_security import io, vault
    from tests.e2e.vault_home import seal_fixture_home

    home = tmp_path / "owner"
    home.mkdir()
    seal_fixture_home(home)
    source, destination = home / "profiles/old", home / "profiles/new"
    source.mkdir(parents=True)
    io.write_text(source / "config.yaml", "agent: {max_turns: 23}", purpose="config")
    original = (source / "config.yaml").read_bytes()
    rename = os.rename

    def fail_destination(old, new, *args, **kwargs):
        if Path(new) == destination:
            raise OSError("injected destination publish refusal")
        return rename(old, new, *args, **kwargs)

    monkeypatch.setattr(os, "rename", fail_destination)
    try:
        with pytest.raises(OSError, match="destination publish refusal"):
            rename_profile_state(source, destination)
        assert (source / "config.yaml").read_bytes() == original
        assert io.read_text(source / "config.yaml", purpose="config") == "agent: {max_turns: 23}"
        assert not destination.exists()
        assert not list(source.parent.glob(".*.renaming-*"))
        assert not list(source.parent.glob(".new.staging-*"))
    finally:
        vault.lock_now(home)
