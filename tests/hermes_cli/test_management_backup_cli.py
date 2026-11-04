"""Bounded real-process management cohort; original assertions and fixtures retained."""

from __future__ import annotations
import contextlib
import os
import secrets
import signal
import subprocess
import pytest
from tests.hermes_cli.test_management_public_cli import (ROOT, _fresh_unlock)

pytest_plugins = ["tests.hermes_cli.test_management_public_cli"]
pytestmark = [pytest.mark.platforms("linux", "macos"), pytest.mark.live_system_guard_bypass]


def test_rotated_vault_public_backup_import_restores_config_and_history(world, tmp_path):
    from hermes_cli._launchers import runtime_command
    from hermes_state import SessionDB

    def command(args):
        proc = subprocess.Popen(
            runtime_command(ROOT, args), env=world.env, cwd=ROOT,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=180)
            assert proc.returncode == 0, (stdout, stderr)
            with pytest.raises(ProcessLookupError):
                os.killpg(proc.pid, 0)
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate(timeout=10)

    new_password = secrets.token_urlsafe(32)
    result, output = world.call({"op": "passphrase.change"}, secret=new_password)
    assert result.returncode != 0 and output["error"]["code"] == "consent_required", (output, result.stderr)
    from hermes_security.passphrase import change_passphrase
    change_passphrase(world.home, new_password)
    original = world.get()["value"]
    with SessionDB(world.home / "state.db") as db:
        history = db.get_messages("management-history")
    archive = tmp_path / "rotated-vault.zip"
    command(["backup", "--output", str(archive)])
    assert archive.is_file() and archive.stat().st_size > 0
    result, output = world.call({"op": "config.set", "revision": world.get()["revision"],
                                "path": ["agent", "max_turns"], "value": 73})
    assert result.returncode == 0, (output, result.stderr)
    assert world.get(["agent", "max_turns"])["value"] == 73
    command(["import", str(archive), "--force"])
    assert world.get()["value"] == original
    with SessionDB(world.home / "state.db") as db:
        assert db.get_messages("management-history") == history
        assert db._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert _fresh_unlock(world, world.password).returncode != 0
    assert _fresh_unlock(world, new_password).returncode == 0
