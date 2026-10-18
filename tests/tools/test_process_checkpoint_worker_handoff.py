"""processes.json keeps the worker handoffs its writer does not track (ahrazzle B2-04).

A managed worker's background process reaches the next interpreter only through the profile
checkpoint. The owner (or a second worker) rewrote that file from its own table alone, so its
next process start/exit dropped the worker's entry: nobody tailed the log, notify_on_complete
never fired and poll/kill could not see the process. Real processes, real checkpoint file.
"""
import subprocess
import sys
import time

import psutil
import pytest
from hermes_security import frames, io as state_io
from tests.e2e.vault_home import seal_fixture_home


@pytest.mark.platforms("linux", "macos")
def test_owner_checkpoint_write_keeps_a_live_worker_handoff(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    seal_fixture_home(tmp_path)
    from tools import process_registry as pr
    from tools.process_registry_output_log import output_dir
    checkpoint = tmp_path / 'processes.json'
    monkeypatch.setattr(pr, 'CHECKPOINT_PATH', checkpoint)
    logs = output_dir()
    logs.mkdir(parents=True)
    worker_proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdin=subprocess.DEVNULL)
    owner_proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdin=subprocess.DEVNULL)
    try:
        handoff = {'session_id': 'proc_worker', 'command': 'dev-server', 'pid': worker_proc.pid, 'pid_scope': 'host',
                   'host_start_time': pr.ProcessRegistry._safe_host_start_time(worker_proc.pid),
                   'started_at': time.time(), 'output_log': str(logs / 'proc_worker.log'), 'notify_on_complete': True}
        frames.append(logs / 'proc_worker.log', b'serving\n', purpose="log")
        gone = {**handoff, 'session_id': 'proc_gone', 'pid': 2 ** 22 + 7, 'output_log': str(logs / 'proc_gone.log')}
        frames.append(logs / 'proc_gone.log', b'', purpose="log")
        state_io.write_json(checkpoint, [handoff, gone], purpose="state")
        owner = pr.ProcessRegistry()
        owner._running['proc_owner'] = pr.ProcessSession(
            id='proc_owner', command='owner-job', task_id='t', started_at=time.time(), pid=owner_proc.pid,
            pid_scope='host', host_start_time=pr.ProcessRegistry._safe_host_start_time(owner_proc.pid))
        owner._write_checkpoint()  # the owner's own process start
        ids = {entry['session_id'] for entry in state_io.read_json(checkpoint, purpose="state")}
        assert ids == {'proc_owner', 'proc_worker'}, ids
        # The next interpreter still adopts the worker's process from the file.
        successor = pr.ProcessRegistry()
        successor._running['proc_owner'] = owner._running['proc_owner']
        assert successor.recover_from_checkpoint() == 1
        assert successor.get('proc_worker') is not None
        deadline = time.monotonic() + 5
        while 'serving' not in successor.read_log('proc_worker')['output']:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert worker_proc.poll() is None
    finally:
        for proc in (worker_proc, owner_proc):
            proc.kill()
            proc.wait(timeout=10)
    assert not psutil.pid_exists(worker_proc.pid) or psutil.Process(worker_proc.pid).status() == psutil.STATUS_ZOMBIE
