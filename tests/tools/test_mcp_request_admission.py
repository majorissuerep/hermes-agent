"""A queued MCP command retains its original Source before any remote effect.

Real synthetic Vault/Source and loop hop; RPC and human consent are test doubles.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from types import SimpleNamespace

import pytest

pytest_plugins = ['tests.tools.test_mcp_private_media',
                  'tests.tools.test_mcp_original_operation_retention']


@pytest.mark.parametrize('boundary', ['lookup', 'approval'])
def test_revoked_original_request_never_dispatches_but_shared_sibling_still_works(
        home, monkeypatch, mcp_background_loop, boundary):
    from agent.session_context import scoped_current_session_id
    from hermes_security.session_authority import start_session_authority
    from tools import mcp_tool, mcp_tool_handlers as handlers
    from tools import approval_prompt

    first = start_session_authority(home, 'admission-A-'+boundary)
    sibling = start_session_authority(home, 'admission-B-'+boundary)
    calls, fresh = [], False

    async def rpc(*_args, **_kwargs):
        calls.append('effect')
        return SimpleNamespace(isError=False, content=[SimpleNamespace(type='text', text='accepted')])

    server = SimpleNamespace(session=SimpleNamespace(call_tool=rpc), _rpc_lock=asyncio.Lock())
    monkeypatch.setattr(mcp_tool, '_servers', {'admission': server})
    monkeypatch.setattr(mcp_tool, '_server_trust_levels',
                        {'admission': mcp_tool._TRUST_UNTRUSTED if boundary == 'approval' else mcp_tool._TRUST_FULL})
    failures = []
    monkeypatch.setattr(mcp_tool, '_bump_server_error', lambda *_: failures.append('strike'))
    original = handlers._acquire_call_server

    def lookup(*args):
        selected = original(*args)
        if boundary == 'lookup' and not fresh:
            first.close()
        return selected

    def consent(*_args, **_kwargs):
        if not fresh:
            first.close()
        return 'accept'

    monkeypatch.setattr(handlers, '_acquire_call_server', lookup)
    monkeypatch.setattr(approval_prompt, 'request_elicitation_consent', consent)
    handle = handlers._make_tool_handler('admission', 'write', 10)
    try:
        with scoped_current_session_id(first.session_id):
            denied = json.loads(handle({}))
        assert calls == [], 'Revoked request reached the RPC effect boundary'
        assert denied.get('error') and failures == []
        assert sibling.is_live()
        fresh = True
        with scoped_current_session_id(sibling.session_id):
            accepted = json.loads(handle({}))
        assert calls == ['effect'] and accepted['result'] == 'accepted' and failures == []
    finally:
        first.close()
        sibling.close()


@pytest.mark.parametrize('operation', ['tool', 'resource'])
@pytest.mark.parametrize('change', ['source-close', 'vault-reunlock'])
def test_queued_rpc_rechecks_original_owner_after_lock_without_breaking_sibling(
        home, monkeypatch, mcp_background_loop, operation, change):
    from agent.session_context import scoped_current_session_id
    from hermes_security import vault
    from hermes_security.session_authority import start_session_authority
    from tools import mcp_tool, mcp_tool_handlers as handlers

    first = start_session_authority(home, 'queued-A-'+operation+'-'+change)
    sibling = start_session_authority(home, 'queued-B-'+operation+'-'+change)
    waiting, calls, failures = threading.Event(), [], []

    class Queue:
        def __init__(self):
            self.lock = asyncio.Lock()

        async def __aenter__(self):
            waiting.set()
            await self.lock.acquire()

        async def __aexit__(self, *_args):
            self.lock.release()

    queue = Queue()

    async def rpc(*_args, **_kwargs):
        calls.append('effect')
        return SimpleNamespace(isError=False, content=[SimpleNamespace(type='text', text='accepted')],
            contents=[SimpleNamespace(uri='memory://admission', mimeType='text/plain', text='accepted')])

    server = SimpleNamespace(session=SimpleNamespace(call_tool=rpc, read_resource=rpc), _rpc_lock=queue)
    monkeypatch.setattr(mcp_tool, '_servers', {'queued': server})
    monkeypatch.setattr(mcp_tool, '_bump_server_error', lambda *_: failures.append('strike'))
    handler = (handlers._make_tool_handler('queued', 'write', 15) if operation == 'tool'
               else handlers._make_read_resource_handler('queued', 15))
    arguments = {} if operation == 'tool' else {'uri': 'memory://admission'}

    def invoke(source):
        with scoped_current_session_id(source.session_id):
            return json.loads(handler(arguments))

    try:
        assert asyncio.run_coroutine_threadsafe(queue.lock.acquire(), mcp_background_loop).result(10)
        with ThreadPoolExecutor(max_workers=1) as workers:
            pending = workers.submit(invoke, first)
            try:
                assert waiting.wait(10)
                if change == 'source-close':
                    first.close()
                else:
                    vault.lock_now(home)
                    vault.unlock(home, 'synthetic media custody password')
                    sibling.close()
                    sibling = start_session_authority(home, 'queued-B-fresh-'+operation)
            finally:
                mcp_background_loop.call_soon_threadsafe(queue.lock.release)
            denied = pending.result(20)
        assert denied.get('error') and calls == [] and failures == []
        accepted = invoke(sibling)
        assert calls == ['effect'] and 'accepted' in str(accepted) and failures == []
        assert sibling.is_live()
    finally:
        first.close()
        sibling.close()


def test_ended_source_after_write_never_recovers_or_invites_duplicate_effect(
        home, monkeypatch, mcp_background_loop):
    from agent.session_context import scoped_current_session_id
    from hermes_security.session_authority import start_session_authority
    from tools import mcp_tool, mcp_tool_handlers as handlers

    first = start_session_authority(home, 'uncertain-A')
    sibling = start_session_authority(home, 'uncertain-B')
    effects, recovery, failures = [], [], []
    fresh = False

    async def rpc(*_args, **_kwargs):
        effects.append('effect')
        if not fresh:
            first.close()
            raise RuntimeError('Synthetic response lost after effect')
        return SimpleNamespace(isError=False, content=[SimpleNamespace(type='text', text='accepted')])

    server = SimpleNamespace(session=SimpleNamespace(call_tool=rpc), _rpc_lock=asyncio.Lock())
    monkeypatch.setattr(mcp_tool, '_servers', {'uncertain': server})
    monkeypatch.setattr(mcp_tool, '_bump_server_error', lambda *_args, **_kwargs: failures.append('strike'))
    def recover(*_args, **_kwargs):
        recovery.append('attempt')
    for name in ('_handle_stdio_child_exited_and_retry', '_handle_auth_error_and_retry',
                 '_handle_session_expired_and_retry'):
        monkeypatch.setattr(handlers, name, recover)
    handler = handlers._make_tool_handler('uncertain', 'write', 15)
    try:
        with scoped_current_session_id(first.session_id):
            denied = json.loads(handler({}))
        assert denied.get('outcome_uncertain') is True
        assert effects == ['effect'] and recovery == [] and failures == []
        assert sibling.is_live()
        fresh = True
        with scoped_current_session_id(sibling.session_id):
            assert json.loads(handler({}))['result'] == 'accepted'
        assert effects == ['effect', 'effect'] and recovery == [] and failures == []
    finally:
        first.close()
        sibling.close()
