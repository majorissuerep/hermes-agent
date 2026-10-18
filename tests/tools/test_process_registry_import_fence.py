"""Importing a tool module must never open the canonical store.

Only the runtime that owns delegation state (gateway authority, interactive agent) may
restore durable completions, and only explicitly; an ordinary client CLI (``hermes cron
run``, ``hermes sessions list``) imports ``tools.process_registry`` transitively and must
stay a zero-writer against ``state.db``.
"""
import json
import os
import queue
import subprocess
import sys
import time

import pytest

_IMPORT_PROBE = r'''
import json, os, sys
home = os.environ["HERMES_HOME"]
connects = []
sys.addaudithook(lambda ev, a: connects.append(str(a[0])) if ev == "sqlite3.connect" and home in str(a[0]) else None)
import tools.process_registry  # noqa: F401
print(json.dumps(connects))
'''


def test_importing_process_registry_does_not_connect_to_state_db(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERMES_")}
    env.update(HERMES_HOME=str(home), HOME=str(tmp_path), PYTHONPATH=os.getcwd())
    out = subprocess.run([sys.executable, "-c", _IMPORT_PROBE], env=env, capture_output=True, text=True,
                         timeout=120, stdin=subprocess.DEVNULL, check=False)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []
    assert not (home / "state.db").exists()




def _async_return(value):
    async def _f(*a, **k):
        return value
    return _f
