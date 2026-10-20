"""Emergency PTY teardown owns descendants, not neighboring process groups."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
import psutil

from hermes_platform.processes import get_process_start_time, pid_exists, terminate_pid
from tests.tui_runtime.test_terminal_model_pty import TerminalPTY
from tests.waiting import wait_until

pytestmark = [pytest.mark.platforms("linux", "macos"), pytest.mark.live_system_guard_bypass]


def test_close_reaps_detached_owned_child_without_signaling_neighbor(tmp_path):
    child_code = "import os,signal,sys; os.write(int(sys.argv[1]), b'1'); signal.pause()"
    parent_code = """
import json,os,signal,subprocess,sys
from pathlib import Path
reader,writer=os.pipe()
child=subprocess.Popen([sys.executable,'-c',sys.argv[2],str(writer)],
                       pass_fds=(writer,),start_new_session=True)
os.close(writer)
assert os.read(reader,1)==b'1'
os.close(reader)
Path(sys.argv[1]).write_text(json.dumps({'parent':os.getpid(),'child':child.pid}))
print('owned descendant ready',flush=True)
signal.pause()
"""
    ready = tmp_path / "owned-ready.json"
    neighbor = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    neighbor_start = get_process_start_time(neighbor.pid)
    terminal = None
    identities = {}
    try:
        terminal = TerminalPTY(dict(os.environ, HOME=str(tmp_path)), "", tmp_path / "custody.log",
            argv=[sys.executable, "-c", parent_code, str(ready), child_code], project=tmp_path)
        terminal.until(lambda text: ready.is_file() and "owned descendant ready" in text,
                       "owned detached child readiness")
        pids = json.loads(ready.read_text())
        assert pids["parent"] == terminal.process.pid
        assert os.getsid(pids["child"]) == pids["child"] != os.getsid(pids["parent"])
        identities = {pid: get_process_start_time(pid) for pid in pids.values()}
        assert all(start is not None for start in identities.values())
        terminal.close()
        terminal.close()
        assert terminal.process.returncode is not None
        wait_until(lambda: all(not psutil.pid_exists(pid) or get_process_start_time(pid) not in (None, started)
            for pid, started in identities.items()), timeout=10,
            what="owned parent and detached child reaped")
        assert neighbor.poll() is None and get_process_start_time(neighbor.pid) == neighbor_start
        assert terminal.transcript.with_suffix(".metrics.json").is_file()
    finally:
        try:
            if terminal is not None:
                terminal.close()
        finally:
            try:
                for pid, started in identities.items():
                    if started is not None and pid_exists(pid) and get_process_start_time(pid) == started:
                        terminate_pid(pid, force=True, expected_start_time=started)
            finally:
                if neighbor.poll() is None:
                    assert neighbor_start is not None
                    terminate_pid(neighbor.pid, force=True, expected_start_time=neighbor_start)
                neighbor.wait(timeout=10)
