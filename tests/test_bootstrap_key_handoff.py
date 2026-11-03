"""A failed interpreter exec must not leave a stale key-FD number in the environment.

``hermes_bootstrap`` re-keys the trusted exec with a one-shot pipe. When ``execv`` fails, the
pipe is closed; a surviving ``HERMES_VAULT_KEY_FD`` would name whatever descriptor reuses that
number next (adopted as a key source in this process or passed on to a child).
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]

_SCRIPT = """
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
home, missing = Path(sys.argv[2]), sys.argv[3]
from hermes_security import vault
vault._SCRYPT_N = 1024
vault.init_vault(home, "synthetic exec password")
vault.unlock(home, "synthetic exec password")
import hermes_cli.venv_sync as venv_sync
venv_sync.prepare_launch = lambda root, argv: Path(missing)  # a relaunch into the store interpreter is owed
venv_sync.relaunch_command = lambda *args: [missing]
before = set(os.listdir("/proc/self/fd"))
import hermes_bootstrap  # performs the real relaunch attempt at import
after = set(os.listdir("/proc/self/fd"))
print(json.dumps({"key_fd_env": os.environ.get("HERMES_VAULT_KEY_FD"),
                  "leaked_fds": len(after - before) > 0 and any(
                      os.readlink(f"/proc/self/fd/{fd}").startswith("pipe:") for fd in after - before
                      if os.path.exists(f"/proc/self/fd/{fd}"))}))
"""


@pytest.mark.platforms("linux")
def test_failed_relaunch_exec_clears_the_key_fd_number(tmp_path):
    home = tmp_path / "owner"
    home.mkdir()
    env = {name: value for name, value in os.environ.items()
           if name not in {"HERMES_SESSION_VAULT", "HERMES_VAULT_KEY_FD", "HERMES_VAULT_PRIVATE_KEY"}}
    env["HERMES_HOME"] = str(home)
    result = subprocess.run([sys.executable, "-I", "-c", _SCRIPT, str(ROOT), str(home),
                             str(tmp_path / "missing-interpreter")],
                            env=env, cwd=tmp_path, capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr
    assert "source-update completion failed" in result.stderr, "the relaunch exec was not attempted"
    outcome = json.loads(result.stdout.strip().splitlines()[-1])
    assert outcome == {"key_fd_env": None, "leaked_fds": False}
