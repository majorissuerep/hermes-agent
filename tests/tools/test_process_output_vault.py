"""A durable native child keeps encrypted output after its launching interpreter exits."""

import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

import pytest


@pytest.mark.platforms("linux", "macos")
def test_durable_child_output_replays_after_launcher_exit(tmp_path, monkeypatch):
    from hermes_security import frames
    from hermes_state.runtime_ownership import OwnershipConflict, exclusive_maintenance
    from tests.e2e.vault_home import run_unlocked, seal_fixture_home
    from tools.process_registry import ProcessRegistry

    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    seal_fixture_home(home)
    release = tmp_path / "release-child"
    command = shlex.join([sys.executable, "-c", (
        "import time; from pathlib import Path\n"
        "print('PRIVATE_LIVE_ORACLE',flush=True)\n"
        "deadline=time.monotonic()+15\n"
        f"while not Path({str(release)!r}).exists():\n"
        " if time.monotonic()>deadline: raise SystemExit(99)\n"
        " time.sleep(0.05)\n"
        "print('PRIVATE_REPLAY_ORACLE',flush=True); raise SystemExit(7)"
    )])
    script = (
        "from hermes_security.handoff import adopt_inherited_key; adopt_inherited_key(); "
        "from tools.process_registry import ProcessRegistry; "
        f"r=ProcessRegistry(); s=r.spawn_local({command!r},persist_on_release=True); "
        "print(s.id,flush=True)"
    )
    launched = run_unlocked([sys.executable, "-c", script], env=os.environ.copy(),
                            capture_output=True, text=True, timeout=15, check=True)
    sid = launched.stdout.strip()
    assert sid.startswith("proc_"), launched
    log = home / "logs" / "process-output" / f"{sid}.log"
    registry = ProcessRegistry()
    try:
        assert registry.recover_from_checkpoint() == 1
        deadline = time.monotonic() + 5
        while "PRIVATE_LIVE_ORACLE" not in registry.read_log(sid)["output"]:
            assert time.monotonic() < deadline, registry.poll(sid)
            time.sleep(0.05)
        assert registry.poll(sid)["status"] == "running"
        with pytest.raises(OwnershipConflict), exclusive_maintenance([home]):
            pytest.fail("durable writer allowed destructive profile maintenance")
        assert b"PRIVATE_LIVE_ORACLE" not in log.read_bytes()
        release.touch()  # The original launcher has exited before the child can continue.
        result = registry.wait(sid, timeout=10)
        assert result["exit_code"] == 7, result
        with exclusive_maintenance([home]):
            pass
        text = "".join(frame.decode() for frame in frames.read_frames(log, purpose="log"))
        assert text.count("PRIVATE_LIVE_ORACLE") == 1
        assert text.count("PRIVATE_REPLAY_ORACLE") == 1
        assert "PRIVATE_LIVE_ORACLE" in registry.read_log(sid)["output"]
        raw = log.read_bytes()
        assert b"PRIVATE_LIVE_ORACLE" not in raw
        assert b"PRIVATE_REPLAY_ORACLE" not in raw
    finally:
        release.touch()
        session = registry.get(sid)
        if session is not None and not session.exited:
            registry.kill_process(sid)


@pytest.mark.platforms("linux", "macos")
def test_output_worker_refuses_bad_handoff_before_starting_command(tmp_path, monkeypatch):
    from hermes_security.handoff import KEY_FD_ENV
    from tests.e2e.vault_home import seal_fixture_home
    from tools import process_registry_output_log

    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    seal_fixture_home(home)
    marker = tmp_path / "must-not-execute"
    log = home / "logs" / "process-output" / "proc_refused.log"
    key_read, key_write = os.pipe()
    ready_read, ready_write = os.pipe()
    os.write(key_write, b"invalid key handoff")
    os.close(key_write)
    worker = Path(process_registry_output_log.__file__).with_name("process_output_worker.py")
    try:
        result = subprocess.run(
            [sys.executable, str(worker), str(log), str(ready_write), "proc_refused",
             sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
            env={**os.environ, KEY_FD_ENV: str(key_read)}, pass_fds=(key_read, ready_write),
            capture_output=True, timeout=15, start_new_session=True, check=False,
        )
        os.close(ready_write)
        ready_write = -1
        assert result.returncode != 0
        assert os.read(ready_read, 1) == b""
        assert not marker.exists()
        assert not log.exists()
    finally:
        os.close(key_read)
        os.close(ready_read)
        if ready_write >= 0:
            os.close(ready_write)
