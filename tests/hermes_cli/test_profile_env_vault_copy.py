"""Profile credential copies must authenticate the source and bind the destination path/key."""
from pathlib import Path

import pytest


def test_backfill_rebinds_root_credentials_and_preserves_existing_profile(tmp_path, monkeypatch):
    from hermes_cli import profiles
    from hermes_security.io import read_text
    from tests.e2e.vault_home import seal_fixture_home

    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text("model:\n  default: fixture\n")
    (home / ".env").write_text("ACME_TOKEN=opaque-root-fixture\n")
    for name in ("missing", "existing"):
        profile = home / "profiles" / name
        profile.mkdir(parents=True)
        (profile / "config.yaml").write_text("model:\n  default: fixture\n")
    existing = home / "profiles/existing/.env"
    existing.write_text("ACME_TOKEN=independent-fixture\n")
    seal_fixture_home(home)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: home)
    monkeypatch.setattr(profiles, "_iter_named_profile_dirs", lambda: iter([home / "profiles/missing", existing.parent]))
    before_root, before_existing = (home / ".env").read_bytes(), existing.read_bytes()
    assert profiles.backfill_profile_envs(quiet=True) == ["missing"]
    target = home / "profiles/missing/.env"
    assert read_text(target, purpose="env") == "ACME_TOKEN=opaque-root-fixture\n"
    assert target.read_bytes() != before_root and b"opaque-root-fixture" not in target.read_bytes()
    assert (home / ".env").read_bytes() == before_root and existing.read_bytes() == before_existing
    assert target.stat().st_mode & 0o777 == 0o600
    assert profiles.backfill_profile_envs(quiet=True) == []


@pytest.mark.parametrize("relative,purpose", [(".env", "env"), ("config.yaml", "config"),
                                               ("memories/MEMORY.md", "memory"), ("SOUL.md", "state")])
def test_profile_file_copy_reencrypts_for_an_independent_destination_vault(tmp_path, monkeypatch, relative, purpose):
    from hermes_cli.profiles import _clone_file
    from hermes_security.io import read_text, write_text
    from tests.e2e.vault_home import seal_fixture_home

    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    seal_fixture_home(source)
    seal_fixture_home(destination)
    monkeypatch.setenv("HERMES_HOME", str(destination))
    write_text(source / relative, "private clone fixture", purpose=purpose)
    before = (source / relative).read_bytes()
    _clone_file(source, destination, relative)
    assert read_text(destination / relative, purpose=purpose) == "private clone fixture"
    assert (source / relative).read_bytes() == before
    assert (destination / relative).read_bytes() != before
    assert b"private clone fixture" not in (destination / relative).read_bytes()


def test_locked_profile_credentials_are_not_copied_as_opaque_broken_state(tmp_path):
    from hermes_cli.profiles import _clone_file
    from hermes_security import vault
    from hermes_security.errors import VaultLockedError
    from hermes_security.io import write_text
    from tests.e2e.vault_home import seal_fixture_home

    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    seal_fixture_home(source)
    seal_fixture_home(destination)
    write_text(source / ".env", "ACME_TOKEN=private-fixture", purpose="env")
    vault.lock_now(source)
    with pytest.raises(VaultLockedError):
        _clone_file(source, destination, ".env")
    assert not (destination / ".env").exists()
