"""Native filesystem contracts for authenticated profile publication (no app subprocesses)."""

from contextlib import closing
from pathlib import Path

import pytest


@pytest.mark.parametrize("kind", ["fresh", "clone", "clone-all"])
def test_staged_profile_keeps_all_native_state_readable_after_publish(tmp_path, monkeypatch, kind):
    from hermes_cli import profiles
    from hermes_cli.config_defaults import DEFAULT_CONFIG
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from hermes_security import io, sqlite, frames
    from tests.e2e.vault_home import seal_fixture_home

    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        f"_config_version: {DEFAULT_CONFIG['_config_version']}\n"
        "model:\n  provider: litellm\n  default: deepseek/deepseek-v4-flash-0731\n"
        "memory:\n  provider: mem0\n"
    )
    (home / ".env").write_text("ACME_TOKEN=private-profile-source\n")
    seal_fixture_home(home)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: home)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: home / "profiles")
    io.write_text(home / "SOUL.md", "private source identity", purpose="state")
    io.write_text(home / "memories/MEMORY.md", "private source memory", purpose="memory")
    io.write_text(home / "mem0.json", '{"api_key":"private-memory-provider"}', purpose="state")
    (home / "skills/probe").mkdir(parents=True)
    (home / "skills/probe/SKILL.md").write_text("public skill code")
    with closing(sqlite.connect(home / "kanban.db")) as db:
        db.execute("CREATE TABLE retained_work (body TEXT)")
        db.execute("INSERT INTO retained_work VALUES ('unexecuted private work')")
        db.commit()
    frames.append(home / "logs/native.log", b"private log record", purpose="log")
    originals = {name: (home / name).read_bytes() for name in
                 ("config.yaml", ".env", "SOUL.md", "memories/MEMORY.md", "mem0.json", "kanban.db")}
    token = set_hermes_home_override(home)
    try:
        result = profiles.create_profile(
            "child", no_alias=True, no_skills=kind == "fresh", clone_config=kind == "clone",
            clone_all=kind == "clone-all", description="independent profile description",
        )
    finally:
        reset_hermes_home_override(token)
    assert result == home / "profiles/child"
    assert not list(result.parent.glob(".child.staging-*"))
    for name, purpose in (("config.yaml", "config"), (".env", "env"), ("SOUL.md", "state"),
                          ("profile.yaml", "state")):
        assert io.read_text(result / name, purpose=purpose)
        assert (result / name).read_bytes().startswith(b"HRMVAULT\x00")
    assert not (result / ".hermes-vault").exists()
    assert profiles.read_profile_meta(result)["description"] == "independent profile description"
    if kind != "fresh":
        assert io.read_text(result / ".env", purpose="env") == "ACME_TOKEN=private-profile-source\n"
        assert io.read_text(result / "memories/MEMORY.md", purpose="memory") == "private source memory"
        assert "private-memory-provider" in io.read_text(result / "mem0.json", purpose="state")
        assert (result / "skills/probe/SKILL.md").read_text() == "public skill code"
    if kind == "clone-all":
        with closing(sqlite.connect(result / "kanban.db")) as db:
            assert db.execute("SELECT body FROM retained_work").fetchall() == [("unexecuted private work",)]
        assert list(frames.read_frames(result / "logs/native.log", purpose="log")) == [b"private log record"]
    assert all((home / name).read_bytes() == data for name, data in originals.items())


def test_bad_donor_authentication_never_publishes_a_partial_profile(tmp_path, monkeypatch):
    from hermes_cli import profiles
    from hermes_security.errors import VaultIntegrityError
    from tests.e2e.vault_home import seal_fixture_home

    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text("model:\n  provider: litellm\n")
    (home / ".env").write_text("ACME_TOKEN=private-profile-source\n")
    seal_fixture_home(home)
    path = home / ".env"
    damaged = path.read_bytes()[:-1] + bytes([path.read_bytes()[-1] ^ 1])
    path.write_bytes(damaged)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: home)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: home / "profiles")
    with pytest.raises(VaultIntegrityError):
        profiles.create_profile("child", clone_config=True, no_alias=True)
    assert not (home / "profiles/child").exists()
    assert not list((home / "profiles").glob(".child.staging-*"))
    assert path.read_bytes() == damaged
