"""Reboot restoration uses durable deck rows, not all historical sessions."""

from pathlib import Path

import pytest

from hermes_state import SessionDB


def test_restore_after_process_loss_keeps_profiles_and_cwds(tmp_path, monkeypatch):
    from hermes_cli import profiles, session_deck
    from hermes_cli.session_restore import plan_restore

    home = tmp_path / ".hermes"
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: home)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: home / "profiles")
    wanted = []
    for profile in ("default", "work"):
        directory = home if profile == "default" else home / "profiles" / profile
        directory.mkdir(parents=True)
        (directory / "config.yaml").write_text("model: fixture\n")
        cwd = tmp_path / (profile + " project 'quoted'")
        cwd.mkdir()
        db = SessionDB(directory / "state.db")
        sid = profile + "-session"
        db.create_session(sid, source="cli")
        db.append_message(sid, "user", "Resume this work after reboot")
        db.set_session_title(sid, profile + " work")
        handle = db.deck_open(sid, surface="cli", cwd=str(cwd))
        closed = db.deck_open(sid + "-closed", surface="cli", cwd=str(cwd))
        db.deck_close(closed, reason="explicit close")
        db.close()
        wanted.append((profile, sid, str(cwd), handle))

    rows = session_deck.list_deck(["default", "work"])
    targets = plan_restore(rows)
    assert {(r.profile, r.session_id, r.cwd, r.handle) for r in targets} == set(wanted)
    assert all(row["state"] == "dormant" for row in rows)
    assert plan_restore([{**row, "attached": True} for row in rows]) == []
    for db in session_deck._DB_CACHE.values():
        db.close()
    session_deck._DB_CACHE.clear()


@pytest.mark.parametrize("platform, terminal", [("linux", "gnome-terminal"), ("darwin", "Terminal")])
def test_terminal_launch_keeps_quoted_paths_as_data(platform, terminal):
    from hermes_platform.resolver.terminals import terminal_command

    command = ["/path with spaces/python", "-m", "hermes_cli.session_restore", "--attach", "work#3"]
    argv = terminal_command(terminal, command, platform=platform)
    if platform == "linux":
        assert argv == ["gnome-terminal", "--window", "--", *command]
    else:
        assert argv[:2] == ["osascript", "-e"]
        assert "do script" in argv[2]
        assert "'/path with spaces/python'" in argv[2]
    assert not any("powershell" in word or "cmd.exe" in word for word in argv)
