"""Opaque relocation of an independently migrated profile preserves its original keys.

Native filesystem/crypto helpers only; public CLI qualification runs separately
in a disposable environment. Parent-vault clone/rebind contracts remain separate.
"""
from contextlib import closing
from pathlib import Path
import sqlite3

import pytest


@pytest.fixture
def profiles_home(tmp_path, monkeypatch):
    from hermes_cli import profiles
    from hermes_security import vault

    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    (home / "config.yaml").write_text("agent: {max_turns: 23}\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: home)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: home / "profiles")
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    yield home
    vault.clear_vault_cache()


def _bytes(root, names):
    return {name: (root / name).read_bytes() for name in names}


@pytest.mark.platforms("posix")
def test_migrated_own_vault_rename_preserves_credential_cipher_and_child_island(profiles_home):
    from hermes_cli import profiles
    from hermes_security import io, frames, sqlite, vault
    from hermes_security.errors import VaultLockedError
    from hermes_security.migrate import migrate_home

    source = profiles.create_profile("old", no_alias=True, no_skills=True)
    (source / "config.yaml").write_text("agent: {max_turns: 23}\n")
    (source / "profile.yaml").write_text("display_name: Original fixture\n")
    (source / "logs/early.log").write_bytes(b"private migrated log record\n")
    (source / ".env").write_text("ACME_TOKEN=private own-vault credential\n")
    with closing(sqlite3.connect(source / "state.db")) as db:
        db.execute("CREATE TABLE retained(content TEXT)")
        db.execute("INSERT INTO retained VALUES ('private original history')")
        db.commit()
    child = source / "profiles/child"
    child.mkdir(parents=True)
    vault.init_vault(child, "synthetic independent child password")
    child_custody = vault.unlock(child, "synthetic independent child password")
    io.write_text(child / "private.json", '{"private":"child island"}', purpose="state")
    assert migrate_home(source, "synthetic original profile password").ok
    root_custody = vault.unlock(source, "synthetic original profile password")
    sibling = profiles_home / "profiles/sibling"
    vault.init_vault(sibling, "synthetic unrelated sibling password")
    vault.unlock(sibling, "synthetic unrelated sibling password")
    io.write_text(sibling / ".env", "ACME_TOKEN=independent sibling\n", purpose="env")
    names = [".hermes-vault", "config.yaml", "profile.yaml", ".env", "state.db", "logs/early.log",
             "profiles/child/.hermes-vault", "profiles/child/private.json"]
    before = _bytes(source, names)
    sibling_before = _bytes(sibling, [".hermes-vault", ".env"])
    destination = profiles.rename_profile("old", "new")
    assert not source.exists() and _bytes(destination, names) == before
    assert not (profiles_home / ".hermes-vault").exists(), "rename must not manufacture parent authority"
    assert _bytes(sibling, sibling_before) == sibling_before and vault.is_unlocked(sibling)
    with pytest.raises(VaultLockedError):
        root_custody.assert_custody_live()
    with pytest.raises(VaultLockedError):
        child_custody.assert_custody_live()
    assert not vault.is_unlocked(destination)
    vault.unlock(destination, "synthetic original profile password")
    assert io.read_text(destination / ".env", purpose="env") == "ACME_TOKEN=private own-vault credential\n"
    assert b"".join(frames.read_frames(destination / "logs/early.log", purpose="log")) == b"private migrated log record\n"
    with closing(sqlite.connect(destination / "state.db")) as db:
        assert db.execute("SELECT content FROM retained").fetchall() == [("private original history",)]
    renamed_child = destination / "profiles/child"
    vault.unlock(renamed_child, "synthetic independent child password")
    assert io.read_text(renamed_child / "private.json", purpose="state") == '{"private":"child island"}'
    assert _bytes(destination, names) == before


@pytest.mark.platforms("linux", "macos")
@pytest.mark.parametrize("obstruction", ["active-child", "destination-race"])
def test_rename_admission_refuses_without_moving_or_overwriting_user_state(profiles_home, monkeypatch, obstruction):
    from hermes_cli import profiles
    from hermes_security import io, vault
    from hermes_state.runtime_ownership import OwnershipConflict, ProfileOwnership
    from tools import mcp_tool_lifecycle

    source = profiles.create_profile("old", no_alias=True, no_skills=True)
    (source / "config.yaml").write_text("agent: {max_turns: 23}\n")
    child = source / "profiles/child"
    child.mkdir(parents=True)
    vault.init_vault(child, "synthetic child admission password")
    vault.unlock(child, "synthetic child admission password")
    io.write_text(child / "private.json", '{"preserve":"original child"}', purpose="state")
    before = _bytes(source, ["config.yaml", "profiles/child/.hermes-vault", "profiles/child/private.json"])
    destination = source.parent / "new"
    owner = ProfileOwnership()
    marker = {}
    if obstruction == "active-child":
        owner.reserve([child])
        expected = OwnershipConflict
    else:
        shutdown = mcp_tool_lifecycle.shutdown_mcp_servers
        def raced_destination(**kwargs):
            shutdown(**kwargs)
            destination.mkdir(mode=0o700)
            marker["identity"] = (destination.stat().st_dev, destination.stat().st_ino)
        monkeypatch.setattr(mcp_tool_lifecycle, "shutdown_mcp_servers", raced_destination)
        expected = FileExistsError
    try:
        with pytest.raises(expected):
            profiles.rename_profile("old", "new")
        assert source.is_dir() and _bytes(source, before) == before
        assert vault.is_unlocked(child)
        assert io.read_text(child / "private.json", purpose="state") == '{"preserve":"original child"}'
        if obstruction == "destination-race":
            assert (destination.stat().st_dev, destination.stat().st_ino) == marker["identity"]
            assert list(destination.iterdir()) == []
        else:
            assert not destination.exists()
    finally:
        owner.close()
