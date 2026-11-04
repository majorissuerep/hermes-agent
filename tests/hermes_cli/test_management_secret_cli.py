"""Bounded real-process management cohort; original assertions and fixtures retained."""

from __future__ import annotations
import contextlib
import json
import os
import secrets
import signal
import subprocess
import pytest
from tests.hermes_cli.test_management_public_cli import (ROOT)

pytest_plugins = ["tests.hermes_cli.test_management_public_cli"]
pytestmark = [pytest.mark.platforms("linux", "macos"), pytest.mark.live_system_guard_bypass]


def test_stalled_secret_pipe_refuses_without_rotating_or_leaving_a_child(world):
    from hermes_cli._launchers import runtime_command

    names = (".hermes-vault", "config.yaml", "state.db")
    before = {name: (world.home / name).read_bytes() for name in names}
    secret = secrets.token_urlsafe(32)
    reader, writer = os.pipe()
    proc = None
    try:
        # The writer deliberately remains open through the refusal: valid bytes are not EOF.
        os.write(writer, secret.encode("utf-8"))
        proc = subprocess.Popen(
            runtime_command(ROOT, ["manage", "apply"]), env=world.env, cwd=ROOT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, pass_fds=(reader,), start_new_session=True,
        )
        stdout, stderr = proc.communicate(
            json.dumps({"op": "passphrase.change", "secret_fd": reader}), timeout=60,
        )
        output = json.loads(stdout)
        assert proc.returncode != 0 and output["version"] == 1 and output["ok"] is False, (output, stderr)
        assert output["error"]["code"] == "consent_required", output
        assert secret not in stdout + stderr
        assert before == {name: (world.home / name).read_bytes() for name in names}
        with pytest.raises(ProcessLookupError):
            os.killpg(proc.pid, 0)
    finally:
        os.close(reader)
        os.close(writer)
        if proc is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate(timeout=10)
    assert world.get(["agent", "max_turns"])["value"] == 19
