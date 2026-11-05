"""Linux outgoing tar helpers cannot outlive their original transfer owner.

Native child-lifetime contract; run under the disposable qualification's Tini.
No browser, provider, external host or real user state is involved.
PID markers are published atomically so existence proves the complete PID is readable.
"""
import os
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tarfile

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
                      f"ready=Path({str(ready)!r}); staging=ready.with_name(ready.name+'.tmp')\n"
                      "staging.write_text(str(os.getpid())); staging.replace(ready)\n"
                      "while True: signal.pause()\n")
    worker.chmod(0o700)
    command = remote_profile.command("export", str(profile), maximum=1024**2)
    command[0] = sys.executable
    env = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"]}
    with subprocess.Popen(command, env=env, stdin=subprocess.PIPE,
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


@pytest.mark.platforms("linux")
@pytest.mark.parametrize("operation", ["export", "retire"])
def test_transfer_owner_pipe_eof_reaps_helper_and_tar_without_retiring_source(tmp_path, operation):
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    original = profile / "private-cookie-state"
    original.write_bytes(b"synthetic-original-profile-must-survive")
    binary = tmp_path / "public-bin"
    binary.mkdir(mode=0o700)
    ready = tmp_path / "worker-identity"
    worker = binary / "tar"
    worker.write_text("#!/usr/bin/env python3\nimport os,signal\nfrom pathlib import Path\n"
                      f"ready=Path({str(ready)!r}); staging=ready.with_name(ready.name+'.tmp')\n"
                      "staging.write_text(str(os.getpid())); staging.replace(ready)\n"
                      "while True: signal.pause()\n")
    worker.chmod(0o700)
    command = remote_profile.command(operation, str(profile), maximum=1024**2,
                                     expected="a" * 64 if operation == "retire" else "")
    command[0] = sys.executable
    env = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"]}
    with subprocess.Popen(command, env=env, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) as helper:
        try:
            assert wait_until(ready.is_file, timeout=10)
            pid = int(ready.read_text())
            start = get_process_start_time(pid)
            assert start and pid_exists(pid) and helper.poll() is None
            helper.stdin.close()  # real anonymous owner-pipe EOF; no signal or caller-selected PID
            assert wait_until(lambda: helper.poll() is not None, timeout=10)
            assert helper.returncode != 0
            assert wait_until(lambda: not pid_exists(pid) or get_process_start_time(pid) != start, timeout=10)
            assert original.read_bytes() == b"synthetic-original-profile-must-survive"
            ready.unlink()
            refused = subprocess.run(command, env=env, stdin=subprocess.DEVNULL,
                                     capture_output=True, timeout=10, check=False)
            assert refused.returncode != 0 and not ready.exists()
            assert original.read_bytes() == b"synthetic-original-profile-must-survive"
        finally:
            if helper.poll() is None:
                helper.kill()
                helper.wait(timeout=5)


@pytest.mark.platforms("linux")
def test_held_owner_pipe_permits_natural_export_and_verified_retirement(tmp_path):
    from hermes_platform.client_capture import run_client

    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    original = b"synthetic-private-profile-for-natural-completion"
    (profile / "Cookies").write_bytes(original)
    command = remote_profile.command("export", str(profile), maximum=1024**2)
    command[0] = sys.executable
    code, output, _error = run_client(command, env=os.environ.copy(), timeout=10,
                                      keep_input_open=True, max_output=1024**2)
    assert code == 0
    with tarfile.open(fileobj=io.BytesIO(output), mode="r:") as incoming:
        assert incoming.extractfile("./Cookies").read() == original
    assert (profile / "Cookies").read_bytes() == original
    command = remote_profile.command("retire", str(profile), maximum=1024**2,
                                     expected=hashlib.sha256(output).hexdigest())
    command[0] = sys.executable
    code, _output, _error = run_client(command, env=os.environ.copy(), timeout=10,
                                       keep_input_open=True)
    assert code == 0 and list(profile.iterdir()) == []


@pytest.mark.platforms("linux")
@pytest.mark.parametrize("operation", ["export", "retire"])
def test_terminal_owner_event_does_not_interrupt_a_slow_owned_child_reap(tmp_path, operation):
    import time

    profile, binary = tmp_path / "profile", tmp_path / "public-bin"
    profile.mkdir(mode=0o700)
    binary.mkdir(mode=0o700)
    original = profile / "Cookies"
    original.write_bytes(b"synthetic-state-retained-during-slow-reap")
    ready, reaping, release = (tmp_path / name for name in ("worker", "reaping", "release"))
    worker = binary / "tar"
    worker.write_text("#!/usr/bin/env python3\nimport os,signal\nfrom pathlib import Path\n"
                      f"ready=Path({str(ready)!r}); staging=ready.with_name(ready.name+'.tmp')\n"
                      "staging.write_text(str(os.getpid())); staging.replace(ready)\n"
                      "while True: signal.pause()\n")
    worker.chmod(0o700)
    command = remote_profile.command(operation, str(profile), maximum=1024**2,
                                     expected="a" * 64 if operation == "retire" else "")
    # Execute the actual public helper payload with only its child-reaping seam
    # delayed. Tar really receives SIGKILL; no production timer/test flag changes.
    wrapper = (
        "import subprocess,sys,threading\nfrom pathlib import Path\n"
        "real_kill,real_wait=subprocess.Popen.kill,subprocess.Popen.wait\n"
        "def kill(child):\n"
        " result=real_kill(child);child.test_reaping=True;return result\n"
        "def wait(child,*args,**kwargs):\n"
        " if getattr(child,'test_reaping',False):\n"
        f"  Path({str(reaping)!r}).touch()\n"
        f"  while not Path({str(release)!r}).exists():threading.Event().wait(.01)\n"
        " return real_wait(child,*args,**kwargs)\n"
        "subprocess.Popen.kill,subprocess.Popen.wait=kill,wait\n"
        "payload=sys.argv[1];sys.argv=['fixed-profile-helper',*sys.argv[2:]]\n"
        "exec(payload,{'__name__':'__main__'})\n"
    )
    env = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"]}
    with subprocess.Popen([sys.executable, "-I", "-c", wrapper, *command[3:]], env=env,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as helper:
        try:
            assert wait_until(ready.is_file, timeout=10)
            pid = int(ready.read_text())
            start = get_process_start_time(pid)
            assert start and pid_exists(pid)
            helper.stdin.close()
            assert wait_until(reaping.is_file, timeout=10)
            observe_until = time.monotonic() + 2
            wait_until(lambda: helper.poll() is not None or time.monotonic() >= observe_until, timeout=5)
            assert helper.poll() is None, "a repeated lifetime signal interrupted owned-child reaping"
            release.touch()
            assert wait_until(lambda: helper.poll() is not None, timeout=10)
            assert helper.returncode != 0
            assert wait_until(lambda: not pid_exists(pid) or get_process_start_time(pid) != start, timeout=10)
            assert original.read_bytes() == b"synthetic-state-retained-during-slow-reap"
        finally:
            release.touch()
            if helper.poll() is None:
                helper.kill()
                helper.wait(timeout=5)
