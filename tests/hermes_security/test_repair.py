"""``secure-vault repair`` contracts, from the luoman-MS73-HB1 incident.

Repair once judged files by "starts with the envelope magic?" — SQLCipher pages and frame streams have
no magic, so it wrapped every DB and log in an envelope (DBs unreadable, logs read back empty) and sealed
a sanitizer-mangled ``.env`` as if it were text. Repair must be class-aware and idempotent.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

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
    # Reproduce an OLD writer's invalid stream; current append correctly refuses it.
    with log.open("ab") as handle:
        handle.write(sec_frames.encode_frames([b"appended after the wrap"], vault=vault, purpose="log"))
    env = home / ".env"
    env.write_text(env.read_bytes().decode("utf-8", errors="replace").replace("\x00", ""), encoding="utf-8")

    report = mig.repair_clobbered_state(home)

    assert not report["unrecoverable"], report
    rows, lines, env_text = _state(home)
    assert (rows, env_text) == (expected[0], expected[2])
    assert lines == [*expected[1], b"old venv plaintext", b"appended after the wrap"]
    again = mig.repair_clobbered_state(home)
    assert not any(again[key] for key in ("sealed", "unwrapped", "reframed", "restored", "unrecoverable"))


@pytest.mark.platforms("any")
@pytest.mark.parametrize("has_backup", [True, False])
@pytest.mark.parametrize("env_name", [".env", ".op.env"])
def test_repair_authenticated_env_failure_restores_backup_or_preserves_damage(tmp_path, monkeypatch, has_backup, env_name):
    from hermes_security import io

    home = tmp_path / "home"
    home.mkdir()
    env = home / env_name
    original = b"OPENAI_API_KEY=integrity-recovery-canary\n"
    env.write_bytes(original)
    monkeypatch.setenv("HERMES_HOME", str(home))
    migration = mig.migrate_home(home, PW)
    assert migration.ok
    if not has_backup:
        migration.backup_path.unlink()
    hv.clear_vault_cache()
    hv.unlock(home, PW)
    damaged = bytearray(env.read_bytes())
    damaged[-1] ^= 1
    damaged = bytes(damaged)
    env.write_bytes(damaged)

    report = mig.repair_clobbered_state(home)

    if not has_backup:
        assert report["unrecoverable"] and not report["restored"]
        assert env.read_bytes() == damaged
        return
    assert not report["unrecoverable"], report
    assert len(report["restored"]) == 1
    assert io.read_bytes(env, purpose="env") == original
    assert io.read_bytes(home / (env_name + ".clobbered"), purpose="state") == damaged
    assert env.read_bytes().startswith(b"HRMVAULT\x00")
    again = mig.repair_clobbered_state(home)
    assert not any(again[key] for key in ("sealed", "restored", "unrecoverable"))


@pytest.mark.platforms("any")
@pytest.mark.parametrize("damage", ["plaintext", "authentication"])
def test_repair_cli_uses_key_file_without_terminal_and_keeps_credentials_private(tmp_path, monkeypatch, damage):
    from hermes_security import io

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    expected = "OPENAI_API_KEY=keyfile-repair-private-canary\n"
    (home / ".env").write_bytes(expected.encode("utf-8"))
    assert mig.migrate_home(home, PW).ok
    unlocked = hv.unlock(home, PW)
    private, public = hv.generate_keypair()
    hv.add_key_slot(home, public_key_raw=public, unlocked_vault=unlocked)
    key = tmp_path / "vault.key"
    key.write_bytes(hv._b64e(private).encode("ascii"))
    key.chmod(0o600)
    if damage == "plaintext":
        (home / ".env").write_bytes(expected.encode("utf-8"))
    else:
        damaged = bytearray((home / ".env").read_bytes())
        damaged[-1] ^= 1
        (home / ".env").write_bytes(damaged)
    source = Path(__file__).resolve().parents[2]
    probe = tmp_path / "repair.py"
    probe.write_text('''
import runpy, sys
sys.path.insert(0, sys.argv.pop(1))
sys.argv = ["hermes", "secure-vault", "repair"]
runpy.run_module("hermes_cli.main", run_name="__main__", alter_sys=True)
''', encoding="utf-8")
    env = dict(os.environ, HOME=str(tmp_path), HERMES_HOME=str(home), HERMES_VAULT_PRIVATE_KEY=str(key))
    for name in ("HERMES_MASTER_PASSWORD", "HERMES_VAULT_KEY_FD", "HERMES_UPDATE_VAULT_CHANNEL"):
        env.pop(name, None)
    result = subprocess.run([sys.executable, "-I", str(probe), str(source)],
                            env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, encoding="utf-8", timeout=45,
                            **({"start_new_session": True} if os.name == "posix" else {}))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Unlock master password" not in result.stdout + result.stderr
    assert "private-canary" not in result.stdout + result.stderr
    assert io.read_text(home / ".env", purpose="env", encoding="utf-8-sig") == expected
    assert (home / ".env").read_bytes().startswith(b"HRMVAULT\x00")
