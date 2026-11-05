"""Exact LP-shaped identity uses inert native Python helpers, never a browser/vendor."""
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys

import pytest

from hermes_platform.managed_supervision import SupervisedChild
from hermes_platform.lightpanda_process import owned_child_record, process_record, matches, parse_serve

pytestmark = pytest.mark.platforms('linux')


def test_native_child_identity_requires_original_executable_port_home_and_start(tmp_path):
    (tmp_path/'serve').write_text('import json,os,threading\nprint(json.dumps({"pid":os.getpid()}),flush=True)\nthreading.Event().wait()\n')
    argv = [sys.executable,'serve','--host','127.0.0.1','--port','43217','--http-cache-dir',str(tmp_path/'cache')]
    env = {**os.environ,'HOME':str(tmp_path)}
    child = SupervisedChild(argv, env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(child.process.stdout, selectors.EVENT_READ)
            assert ready.select(timeout=10)
        pid = json.loads(child.process.stdout.readline())['pid']
        record = owned_child_record(child, executable=sys.executable, argv=argv, home=tmp_path)
        assert record is not None and record['pid'] == pid and record['pid'] != child.pid
        assert record['port'] == 43217 and matches(record)
        assert not matches({**record,'start_time':record['start_time']+1})
        changed = [*argv]; changed[changed.index('--port')+1] = '53217'
        assert process_record(pid, executable=sys.executable, argv=changed, home=tmp_path) is None
        assert process_record(pid, executable=Path('/bin/sh'), argv=argv, home=tmp_path) is None
        assert process_record(pid, executable=sys.executable, argv=argv, home=tmp_path/'foreign') is None
    finally:
        assert child.cancel() is not None


@pytest.mark.parametrize('args', [
    ['lp','other','serve','--host','127.0.0.1','--port','43217'],
    ['lp','serve','--host','127.0.0.1','--port','43217','unparsed','43217'],
    ['lp','serve','--host','127.0.0.1','--port','43217','--port','53217'],
    ['lp','serve','--host','0.0.0.0','--port','43217'],
])
def test_membership_or_unknown_arguments_never_identify_a_hermes_serve_target(args):
    assert parse_serve(args) is None


def test_unreadable_possible_legacy_owner_refuses_without_touching_original(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import psutil
    from hermes_platform.lightpanda_process import require_source_files_idle

    source=tmp_path/'original-lightpanda'
    source.mkdir(mode=0o700)
    target=source/'original.log'
    target.write_bytes(b'complete original log before repair')
    class Unreadable:
        pid=os.getpid()
        info={'uids':SimpleNamespace(effective=os.geteuid()),'status':psutil.STATUS_RUNNING}
    def unreadable(_pid):
        raise psutil.AccessDenied(_pid)
    monkeypatch.setattr(psutil,'process_iter',lambda _attrs:iter([Unreadable()]))
    monkeypatch.setattr('hermes_platform.lightpanda_process._native_open_files',unreadable)
    with pytest.raises(RuntimeError,match='possible legacy file owner is unreadable'):
        require_source_files_idle(source,check_alive=lambda:None)
    assert target.read_bytes()==b'complete original log before repair'
