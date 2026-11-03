"""Native proxy supervision streams output and reaps its own cancelled child."""
import os
import sys

import pytest

from hermes_platform.authority_children import NativeChild
from tests.waiting import wait_until

pytestmark = pytest.mark.platforms("linux", "macos")


def _child(tmp_path, code):
    reader, writer = os.pipe()
    os.close(writer)
    try:
        return NativeChild([sys.executable, "-I", "-c", code], env=dict(os.environ), cwd=tmp_path,
                           key_fd=reader, deadline=20)
    finally:
        os.close(reader)


def test_native_child_streams_more_than_one_frame_and_keeps_exit_status(tmp_path):
    child = _child(tmp_path, "import sys; sys.stdout.buffer.write(b'x'*180000); sys.stderr.write('native-error'); sys.exit(7)")
    stdout, stderr = bytearray(), bytearray()

    def drained():
        chunks, done, _, _ = child.poll()
        for stream, chunk in chunks:
            (stdout if stream == "stdout" else stderr).extend(chunk)
        return done

    try:
        assert wait_until(drained, timeout=10)
        assert stdout == b"x" * 180000 and stderr == b"native-error"
        assert child.returncode == 7
    finally:
        child.close()


def test_cancelled_native_child_is_reaped_before_custody_closes(tmp_path):
    child = _child(tmp_path, "import threading; print('ready', flush=True); threading.Event().wait(60)")
    try:
        assert wait_until(lambda: bool(child.poll()[0]), timeout=10)
        child.close()
        assert child.process.poll() is not None
        assert child.finished.is_set()
        assert not child.identity.is_running()
    finally:
        child.close()


def test_failed_supervisor_setup_reaps_the_already_spawned_native_child(tmp_path, monkeypatch):
    import agent.memory_provider
    import hermes_platform.authority_children as supervision

    spawned = []
    popen = supervision.subprocess.Popen

    def remember(*args, **kwargs):
        process = popen(*args, **kwargs)
        spawned.append(process)
        return process

    def refused(*args, **kwargs):
        raise RuntimeError("synthetic supervisor setup refusal")

    monkeypatch.setattr(supervision.subprocess, "Popen", remember)
    monkeypatch.setattr(agent.memory_provider, "spawn_context_thread", refused)
    try:
        with pytest.raises(RuntimeError, match="synthetic supervisor setup refusal"):
            _child(tmp_path, "import threading; threading.Event().wait(60)")
        assert len(spawned) == 1
        assert spawned[0].poll() is not None
        assert spawned[0].stdout.closed and spawned[0].stderr.closed
    finally:
        for process in spawned:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
