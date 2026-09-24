"""``secure-vault repair`` contracts, from the luoman-MS73-HB1 incident.

Repair once judged files by "starts with the envelope magic?" — SQLCipher pages and frame streams have
no magic, so it wrapped every DB and log in an envelope (DBs unreadable, logs read back empty) and sealed
a sanitizer-mangled ``.env`` as if it were text. Repair must be class-aware and idempotent.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from hermes_security import frames as sec_frames
from hermes_security import migrate as mig
from hermes_security import sqlite as hsql
from hermes_security import vault as hv

PW = "repair-test-password"


def _migrated_home(tmp_path: Path, monkeypatch) -> tuple[Path, hv.Vault]:
    home = tmp_path / ".hermes"
    (home / "logs").mkdir(parents=True)
    (home / ".env").write_text("OPENAI_API_KEY=sk-repair-canary\n")
    (home / "logs" / "agent.log").write_text("migrated line\n")
    conn = sqlite3.connect(home / "state.db")
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.execute("INSERT INTO t VALUES ('db-row')")
    conn.commit()
    conn.close()
    rollback = home / "hermes-agent.pre-fork-20260923-215753"
    rollback.mkdir()
    (rollback / "run_agent.py").write_text("# rollback code\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert mig.migrate_home(home, PW).ok
    hv.clear_vault_cache()
    return home, hv.unlock(home, PW)


def _state(home: Path) -> tuple:
    conn = hsql.connect(home / "state.db")
    try:
        rows = conn.execute("SELECT x FROM t").fetchall()
    finally:
        conn.close()
    return (
        rows,
        list(sec_frames.read_frames(home / "logs" / "agent.log", purpose="log")),
        hv.get_vault(home).read_bytes(home / ".env", purpose="env"),
    )


def test_repair_is_a_noop_on_a_healthy_vault(tmp_path, monkeypatch):
    home, _ = _migrated_home(tmp_path, monkeypatch)
    assert (home / "hermes-agent.pre-fork-20260923-215753" / "run_agent.py").read_bytes() == b"# rollback code\n"
    before = _state(home)

    report = mig.repair_clobbered_state(home)

    assert not any(report[key] for key in ("sealed", "unwrapped", "reframed", "restored", "unrecoverable"))
    assert _state(home) == before


def test_repair_recovers_the_clobbered_home(tmp_path, monkeypatch):
    home, vault = _migrated_home(tmp_path, monkeypatch)
    expected = _state(home)
    # The old repair: DB wrapped in an envelope; the log wrapped, then grown by frame appends and an
    # old-venv plaintext line. The old sanitizer: .env decoded errors=replace, NULs stripped, rewritten.
    db = home / "state.db"
    vault.write_bytes(db, db.read_bytes(), purpose="state")
    log = home / "logs" / "agent.log"
    vault.write_bytes(log, log.read_bytes() + b"old venv plaintext\n", purpose="state")
    sec_frames.append(log, b"appended after the wrap", purpose="log")
    env = home / ".env"
    env.write_text(env.read_bytes().decode("utf-8", errors="replace").replace("\x00", ""), encoding="utf-8")

    report = mig.repair_clobbered_state(home)

    assert not report["unrecoverable"], report
    rows, lines, env_text = _state(home)
    assert (rows, env_text) == (expected[0], expected[2])
    assert lines == [*expected[1], b"old venv plaintext", b"appended after the wrap"]
    again = mig.repair_clobbered_state(home)
    assert not any(again[key] for key in ("sealed", "unwrapped", "reframed", "restored", "unrecoverable"))
