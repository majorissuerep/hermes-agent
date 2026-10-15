"""A paused unmapped gateway keeps the home discovery proved for it (#132338 R2 / review thread 7).

Discovery owns a gateway whose environment is unreadable (elevated, another account) when this
install's spawn ledger holds an exact-birth witness of its home. The pause record must keep THAT
home: written as ``home: null`` it read as a pre-home record, whose argv-only readiness match let
another home's gateway with the same selectorless argv retire the paused runtime's debt.

Real processes, each under its own ``HERMES_HOME``, a real ledger entry, the real pause producer and
durable record, and the real readiness predicate. Seams: the OS denying A's environment
(``_pid_environ``), the Windows-only gateway enumeration and the stop itself (never signalled here).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import psutil
import pytest

from gateway.status import get_process_start_time
from hermes_cli import dashboard_procs, gateway, process_identity, update_cmd_holders
from hermes_cli import update_pause_record as pause_record
from hermes_cli.update_cmd_holders import _pause_windows_gateways_for_update, _unmapped_ready_filter


@pytest.fixture
def homes_and_spawn(tmp_path):
    entry = tmp_path / "bin" / "hermes"  # a ``hermes`` entry token: the cmdline reads as a gateway
    entry.parent.mkdir()
    entry.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    argv = [sys.executable, str(entry), "gateway", "run"]
    root = os.environ["HERMES_HOME"]  # the sandboxed home this update owns, with two profiles
    home_a, home_b = (os.path.join(root, "profiles", name) for name in ("a", "b"))
    os.makedirs(home_a)
    os.makedirs(home_b)
    procs: list[subprocess.Popen] = []

    def spawn(home) -> subprocess.Popen:
        proc = subprocess.Popen(argv, env={**os.environ, "HERMES_HOME": str(home)})
        procs.append(proc)
        return proc

    yield home_a, home_b, spawn
    for proc in procs:
        proc.kill()
        proc.wait(timeout=10)




@pytest.mark.platforms("linux")
@pytest.mark.spawns_gateway_lookalike
def test_a_recorded_but_empty_home_is_never_matched_only_a_pre_home_entry_is(homes_and_spawn):
    home_a, home_b, spawn = homes_and_spawn
    old = spawn(home_a)
    argv = list(psutil.Process(old.pid).cmdline())
    born = get_process_start_time(old.pid)
    old.kill()
    old.wait(timeout=10)
    time.sleep(0.05)
    sibling = spawn(home_b)
    assert _unmapped_ready_filter({"pid": old.pid, "argv": argv, "home": None, "ct": born}, set())([sibling.pid]) == [], \
        "a newly incomplete record took the legacy argv-only match"
    # A token from an updater that predates the home field has nothing else to match on.
    assert _unmapped_ready_filter({"pid": old.pid, "argv": argv}, set())([sibling.pid]) == [sibling.pid]
