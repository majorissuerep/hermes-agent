"""Early vault help must reach the operation owner's argument policy first.

Library/parser controls only: no CLI bootstrap, PM, vault, server or child runs.
The existing proxy is real; its stdin/signal and owner wire effects are inert.
"""
import builtins
import hashlib
import sys
from types import ModuleType, SimpleNamespace

import pytest

from hermes_cli import session_proxy
from hermes_cli.vault_startup_admission import admit_vault_command
from hermes_security import session_authority
from hermes_security.authority_native import checked_arguments


@pytest.fixture
def proxy_boundary(tmp_path, monkeypatch):
    home = tmp_path/'home'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.delenv('HERMES_VAULT_KEY_FD', raising=False)
    monkeypatch.setattr('hermes_constants.get_hermes_home', lambda: home)
    monkeypatch.setattr('hermes_constants.get_process_hermes_home', lambda: home)
    monkeypatch.setattr(session_authority, '_vault_home', lambda selected: home)
    monkeypatch.setattr(session_proxy, '_read_input', lambda: b'')
    monkeypatch.setattr(session_proxy, 'signal', SimpleNamespace(SIGTERM=15, signal=lambda *_: None))
    calls = []
    state = SimpleNamespace(revoked=False)

    def owner_request(selected, raw, request):
        assert selected == home and raw == 'synthetic-operation-only-grant'
        calls.append(request)
        if request['op'] == 'native.start':
            if state.revoked:
                return {'ok': False, 'native_error': 'original source ended'}
            try:
                checked_arguments(request['arguments'])
            except ValueError as exc:
                return {'ok': False, 'native_error': str(exc)}
            return {'ok': True, 'job': 'inert-help'}
        if request['op'] == 'native.poll':
            return {'ok': True, 'done': True, 'returncode': 0, 'chunks': []}
        assert request['op'] == 'native.cancel'
        return {'ok': True}

    monkeypatch.setattr(session_authority, 'operation_request', owner_request)
    return home, calls, state


def _vault_view(monkeypatch, *, unlocked):
    view = ModuleType('hermes_security.vault')
    view.is_unlocked = lambda _home: unlocked
    monkeypatch.setitem(sys.modules, 'hermes_security.vault', view)


@pytest.mark.parametrize('revoked', [False, True])
@pytest.mark.parametrize('cached_vault', [False, True])
@pytest.mark.parametrize(('arguments', 'expected'), [
    (['secure-vault', '--help'], 0),
    (['secure-vault', '--help', '--run-module', 'synthetic'], 2),
    (['--model', 'synthetic', 'secure-vault', '--help'], 2),
])
def test_operation_help_reaches_owner_before_early_argparse(
    proxy_boundary, monkeypatch, cached_vault, revoked, arguments, expected,
):
    home, calls, state = proxy_boundary
    state.revoked = revoked
    expected = 2 if revoked else expected
    before = list(home.rglob('*'))
    monkeypatch.setenv('HERMES_SESSION_VAULT', 'synthetic-operation-only-grant')
    if cached_vault:
        _vault_view(monkeypatch, unlocked=False)
    else:
        monkeypatch.delitem(sys.modules, 'hermes_security.vault', raising=False)
        original_import = builtins.__import__

        def no_crypto_import(name, *args, **kwargs):
            assert name != 'hermes_security.vault' and not name.startswith(('cryptography', 'yaml', 'sqlcipher3')), 'pre-PM proxy imported a runtime dependency'
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, '__import__', no_crypto_import)
    with pytest.raises(SystemExit) as caught:
        admit_vault_command(arguments)
    assert caught.value.code == expected
    assert calls and calls[0]['op'] == 'native.start'
    assert calls[0]['arguments'] == arguments
    assert len(calls) == (2 if expected == 0 else 1)
    assert list(home.rglob('*')) == before


@pytest.mark.parametrize('scope', ['standalone', 'cached-native-owner', 'trusted-key-fd'])
def test_local_help_and_key_only_refusal_remain_read_only(proxy_boundary, monkeypatch, capsys, scope):
    home, calls, _state = proxy_boundary
    if scope == 'standalone':
        monkeypatch.delenv('HERMES_SESSION_VAULT', raising=False)
    else:
        monkeypatch.setenv('HERMES_SESSION_VAULT', 'synthetic-operation-only-grant')
        _vault_view(monkeypatch, unlocked=scope == 'cached-native-owner')
        if scope == 'trusted-key-fd':
            monkeypatch.setenv('HERMES_VAULT_KEY_FD', 'inert-presence-only-never-opened')
    sentinel = home/'existing.data'
    sentinel.write_bytes(b'synthetic retained pre-startup bytes')
    def tree():
        return {str(path.relative_to(home)): (path.lstat().st_mode, hashlib.sha256(path.read_bytes()).hexdigest())
                for path in home.rglob('*')}
    before = tree()
    with pytest.raises(SystemExit) as helped:
        admit_vault_command(['secure-vault', 'migrate', '--key-only', '--help'])
    assert helped.value.code == 0 and '--key-out' in capsys.readouterr().out
    for destination in ([], ['--key-out', str(home/'unsafe.key')]):
        with pytest.raises(SystemExit) as refused:
            admit_vault_command(['secure-vault', 'migrate', '--key-only', *destination])
        assert refused.value.code == 2
    assert tree() == before and not calls
