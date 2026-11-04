"""Linux outgoing tar helpers cannot outlive their original transfer owner.

Native child-lifetime contract; run under the disposable qualification's Tini.
No browser, provider, external host or real user state is involved.
"""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from hermes_platform import remote_profile
from hermes_platform.processes import get_process_start_time, pid_exists
from tests.waiting import wait_until


@pytest.mark.platforms("linux")
def test_tar_worker_dies_when_original_transfer_helper_is_killed(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    binary = tmp_path / "public-bin"
    binary.mkdir(mode=0o700)
    ready = tmp_path / "worker-identity"
    # A fixed inert tar stand-in stops after proving the real fork/exec boundary.
    worker = binary / "tar"
    worker.write_text("#!/usr/bin/env python3\nimport os,signal\nfrom pathlib import Path\n"
                      f"Path({str(ready)!r}).write_text(str(os.getpid()))\n"
                      "while True: signal.pause()\n")
    worker.chmod(0o700)
    command = remote_profile.command("export", str(profile), maximum=1024**2)
    command[0] = sys.executable
    env = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"]}
    with subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) as helper:
        try:
            assert wait_until(ready.is_file, timeout=10)
            pid = int(ready.read_text())
            start = get_process_start_time(pid)
            assert start and pid_exists(pid)
            helper.kill()  # unreaped child object: cannot signal a reused PID
            helper.wait(timeout=5)
            assert wait_until(lambda: not pid_exists(pid) or get_process_start_time(pid) != start, timeout=10)
        finally:
            if helper.poll() is None:
                helper.kill()
                helper.wait(timeout=5)
