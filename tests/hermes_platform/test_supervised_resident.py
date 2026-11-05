"""Inert native Python children prove the optional resident-session boundary, no app/vendor."""
import json
import os
import subprocess
import sys

import pytest

from hermes_platform.managed_supervision import SupervisedChild
from hermes_platform.processes import get_process_start_time
from tests.waiting import wait_until

pytestmark = pytest.mark.platforms('linux')

_SCRIPT = '''import json,os,subprocess,sys,threading
nested=subprocess.Popen([sys.executable,'-c','import threading; threading.Event().wait()'])
print(json.dumps({'pid':os.getpid(),'nested':nested.pid,'supervisor':os.getppid(),
                  'session':os.getsid(0),'group':os.getpgrp()}),flush=True)
threading.Event().wait()
'''


@pytest.mark.parametrize('detached', [False, True], ids=['foreground-default','resident-detached'])
@pytest.mark.parametrize('ending', ['cancel','control-EOF'])
def test_resident_session_preserves_default_and_reaps_only_owned_tree(detached, ending):
    sibling = subprocess.Popen([sys.executable,'-c','import threading; threading.Event().wait()'])
    child = SupervisedChild([sys.executable,'-u','-c',_SCRIPT], env=os.environ.copy(),
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=detached)
    try:
        import selectors
        with selectors.DefaultSelector() as ready:
            ready.register(child.process.stdout, selectors.EVENT_READ)
            assert ready.select(timeout=10), 'bounded inert child readiness'
        observed = json.loads(child.process.stdout.readline())
        assert observed['supervisor'] == child.pid
        assert observed['session'] == os.getsid(child.pid)
        if detached:
            assert observed['session'] == child.pid and observed['session'] != os.getsid(0)
            assert observed['group'] == child.pid
        else:
            assert observed['session'] == os.getsid(0) and observed['group'] == os.getpgrp()
        identities = {pid:get_process_start_time(pid) for pid in (observed['pid'],observed['nested'])}
        assert all(start is not None for start in identities.values())
        if ending == 'cancel':
            assert child.cancel() is not None
        else:
            child._close_control()
            child.wait(timeout=20)
        assert wait_until(lambda: all(get_process_start_time(pid) != start for pid,start in identities.items()), timeout=20)
        assert sibling.poll() is None and os.getsid(0) > 0
    finally:
        child.cancel()
        sibling.terminate()
        sibling.wait(timeout=10)
