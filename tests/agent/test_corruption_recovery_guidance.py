"""Regression tests for #88235 — state.db corruption must surface a warning
to the user's messaging platform, not stay silently in the logs.

When SessionDB init fails at gateway startup (corruption, NFS/SMB locks,
disk errors), the gateway sets _session_db = None and logs a warning — but
the user never sees it.  Messages may flow but nothing is persisted, and the
user only discovers the breakage when /resume or session_search comes back
empty.

The fix adds:
1. _session_db_init_error attribute on GatewayRunner, set when init fails
2. _send_session_db_warning_notifications() — broadcasts a recovery-guidance
   message to all home channels after the gateway connects
3. Improved "corrupt" cause wording in _format_turn_completion_explanation
   with the full recovery path (hermes doctor, sqlite3 .recover, backups)
"""







def test_format_turn_completion_corrupt_names_the_sessions_own_store(monkeypatch, tmp_path):
    """Recovery commands target the store that failed, not the process default (#105887).

    A Desktop ``serve`` backend launched on the root home hosts named-profile sessions
    whose SessionDB is ``profiles/<name>/state.db``; guidance built from the process
    default would tell the operator to inspect/repair the root database.
    """
    from run_agent import AIAgent

    root = tmp_path / "root"
    monkeypatch.setenv("HERMES_HOME", str(root))
    failing = root / "profiles" / "research" / "state.db"

    explanation = AIAgent._format_turn_completion_explanation(
        "session_persistence_failed", "corrupt", db_path=failing
    )
    assert f"--source {failing} --inspect-only" in explanation
    assert f"--source {root / 'state.db'}" not in explanation


def test_format_turn_completion_corrupt_never_names_the_live_db():
    """The 'corrupt' cause must not direct a raw sqlite3 shell at the live DB.

    #100368 forensics: the system sqlite3 CLI on Debian/Ubuntu (3.45.1/
    3.46.1, below the 3.51.x WAL-reset fix) unlinks the live WAL/SHM pair
    when pointed at a live state.db, splitting the store into two
    generations whose acknowledged writes vanish. The guidance that ships
    in the corruption banner must be the snapshot-copying
    `hermes sessions recover` lane.
    """
    from run_agent import AIAgent

    explanation = AIAgent._format_turn_completion_explanation(
        "session_persistence_failed", "corrupt"
    )
    assert "sessions recover" in explanation
    assert 'sqlite3 ~/.hermes/state.db ".recover"' not in explanation
    # The replacement guidance names the safe command.
    assert "hermes sessions recover --source" in explanation






def test_corrupt_guidance_pins_the_failing_profile(tmp_path, monkeypatch):
    """#105887: every `hermes ...` command in the recovery guidance (turn explainer, gateway
    home-channel notice, exhausted-repair diagnostic) carries the active profile selector and
    names that profile's state.db. A bare `hermes` follows the sticky ``active_profile`` file,
    so with another profile active the operator would repair the wrong database."""
    import asyncio

    from hermes_state import _default_db_path
    from hermes_state.repair import _persistent_repair_exhausted_error
    from run_agent import AIAgent

    root = tmp_path / "hermes"
    home = root / "profiles" / "research"
    home.mkdir(parents=True)
    (root / "config.yaml").write_text("")
    (root / "active_profile").write_text("other\n")
    monkeypatch.setenv("HERMES_HOME", str(home))

    explanation = AIAgent._format_turn_completion_explanation("session_persistence_failed", "corrupt")
    commands = [line.strip() for line in explanation.splitlines() if "hermes " in line]
    assert commands and all("hermes -p research " in line for line in commands), commands
    # The conftest pins hermes_state.DEFAULT_DB_PATH, so the store named is whatever the
    # process resolves — the contract is "the same path the runtime would open".
    assert f"--source {_default_db_path()} " in explanation

    exhausted = _persistent_repair_exhausted_error(home / "state.db")
    assert "`hermes -p research sessions recover --source" in exhausted
