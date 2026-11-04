"""Fixed repair jobs keep native authority, polling and cancellation ownership.

Helper tests use real vaults/source authorities and fixed subprocesses, not a browser
or public application on the host. Real terminal/SSH qualification is disposable-only.
"""
import base64
import json
import os
from pathlib import Path
import sys
import threading

import pytest

from tests.tools.test_browser_outgoing_continuity import outgoing as _outgoing
from tests.hermes_cli.browser_profile_env import cookie_db
from tests.waiting import wait_until
from hermes_security.session_authority import start_session_authority
from agent.session_context import scoped_current_session_id


@pytest.fixture
def outgoing(tmp_path, monkeypatch):
    yield from _outgoing.__wrapped__(tmp_path, monkeypatch)


def _start(authority, request=None):
    from hermes_security.authority_native import dispatch_native
    return dispatch_native(authority, {'op':'native.start', 'arguments':['manage','apply'],
                           'stdin':base64.b64encode(json.dumps(request or {'op':'browser.outgoing.repair'}).encode()).decode()})


def _orphan(outgoing):
    first=start_session_authority(outgoing.home,'job-A')
    with scoped_current_session_id('job-A'):
        profile=outgoing.start('job-A')
        cookie_db(profile/'Cookies','fixed-job-private-cookie')
    first.close()
    return profile


def test_fixed_native_job_rejects_extra_fields_and_foreign_poll_then_repairs(outgoing):
    from hermes_security.authority_native import dispatch_native, _jobs
    from hermes_security.authority_operations import dispatch_management
    from hermes_cli.management_owned_jobs import OwnedManagementJob
    profile=_orphan(outgoing)
    owner=start_session_authority(outgoing.home,'job-B')
    foreign=start_session_authority(outgoing.home,'job-C')
    try:
        rejected=_start(owner,{'op':'browser.outgoing.repair','target':'foreign'})
        assert rejected['ok'] is False and not _jobs
        blocked=dispatch_management(owner,{'request':{'op':'browser.outgoing.repair'}})
        assert blocked['ok'] is False  # no long synchronous operation under a five-second frame
        started=_start(owner);assert started['ok'] is True
        identity=started['job'];assert isinstance(_jobs[identity][1],OwnedManagementJob)
        assert dispatch_native(foreign,{'op':'native.poll','job':identity})['ok'] is False
        chunks=[]
        def completed():
            reply=dispatch_native(owner,{'op':'native.poll','job':identity})
            assert reply['ok'] is True
            chunks.extend(reply['chunks'])
            return reply if reply['done'] else None
        result=wait_until(completed,timeout=20)
        body=json.loads(b''.join(base64.b64decode(item['data']) for item in chunks))
        assert result['returncode']==0 and body['ok'] is True
        assert body['result']['saved'] and not body['result']['retained']
        assert list(profile.iterdir())==[] and identity not in _jobs
        assert owner.is_live() and foreign.is_live()
    finally:
        owner.close();foreign.close()


@pytest.mark.parametrize('termination',['cancel','source-close'])
def test_job_cancellation_reaps_effect_child_and_retains_domain_recovery(outgoing,monkeypatch,termination,tmp_path):
    from hermes_platform.processes import pid_exists
    from hermes_security.authority_native import dispatch_native, _jobs
    from tools import browser_outgoing_transfer as transfer
    from hermes_security import browser_profile_archive as archive
    profile=_orphan(outgoing)
    owner=start_session_authority(outgoing.home,'cancel-B')
    parent=start_session_authority(outgoing.home,'live-parent')
    ref=next((outgoing.home/'state/browser-outgoing').glob('*.json'))
    original=ref.read_bytes();ready=tmp_path/'effect-pid'
    entered=threading.Event()
    def blocked_download(lease,_ram,**_kwargs):
        entered.set()
        script='import os,signal;from pathlib import Path;Path('+repr(str(ready))+').write_text(str(os.getpid()));signal.pause()'
        transfer.client(lease,[sys.executable,'-c',script])
        raise AssertionError('cancelled effect unexpectedly completed')
    monkeypatch.setattr(transfer,'download',blocked_download)
    try:
        started=_start(owner);assert started['ok'] is True
        identity=started['job'];job=_jobs[identity][1]
        assert entered.wait(10) and wait_until(ready.is_file,timeout=10)
        pid=int(ready.read_text());assert pid_exists(pid)
        if termination=='cancel':
            assert dispatch_native(owner,{'op':'native.cancel','job':identity})['ok'] is True
        else:
            owner.close()
        assert wait_until(lambda:job.finished.is_set() and not pid_exists(pid),timeout=10)
        assert identity not in _jobs
        assert cookie_db(profile/'Cookies')=='fixed-job-private-cookie'
        assert ref.read_bytes()==original and archive.archive_paths(outgoing.home)==[]
        assert parent.is_live()
    finally:
        owner.close();parent.close()


def test_missing_worker_consumer_returns_structured_refusal_without_mutation(outgoing,monkeypatch):
    from hermes_security.authority_native import dispatch_native
    owner=start_session_authority(outgoing.home,'missing-worker-B')
    monkeypatch.setitem(sys.modules,'hermes_cli.browser_worker_repair',None)
    before={p.relative_to(outgoing.home):p.read_bytes() for p in outgoing.home.rglob('*') if p.is_file()}
    try:
        started=_start(owner,{'op':'browser.worker.repair'});assert started['ok'] is True
        chunks=[]
        def complete():
            reply=dispatch_native(owner,{'op':'native.poll','job':started['job']})
            chunks.extend(reply['chunks'])
            return reply if reply['done'] else None
        result=wait_until(complete,timeout=10)
        body=json.loads(b''.join(base64.b64decode(item['data']) for item in chunks))
        assert result['returncode']==1 and body['ok'] is False and body['error']['code']=='state_refused'
        assert {p.relative_to(outgoing.home):p.read_bytes() for p in outgoing.home.rglob('*') if p.is_file()}==before
        assert owner.is_live()
    finally:
        owner.close()
