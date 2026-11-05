"""Formal phases retain the original Source while profile siblings remain usable."""
import json
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('phase', ['before-checker', 'after-checker', 'after-review'])
@pytest.mark.parametrize('change', ['source-close', 'vault-reunlock'])
def test_formal_phase_cannot_borrow_a_sibling_or_new_unlock(tmp_path, monkeypatch, phase, change):
    from agent import formal_logic as mode
    from agent.session_context import scoped_current_session_id
    from hermes_cli import config
    from hermes_security import vault
    from hermes_security.session_authority import start_session_authority

    home = tmp_path/'owner'
    home.mkdir(mode=0o700)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(vault, '_SCRYPT_N', 1024)
    vault.init_vault(home, 'synthetic formal ownership password')
    vault.unlock(home, 'synthetic formal ownership password')
    owner = start_session_authority(home, 'formal-A')
    sibling = start_session_authority(home, 'formal-B')
    agent = SimpleNamespace(_vault_session_authority=owner, _interrupt_requested=False)
    monkeypatch.setattr(config, 'load_config_readonly',
                        lambda: {'agent': {'formal_logic': {'enabled': True}}})
    calls = []

    def revoke():
        if change == 'source-close':
            owner.close()
        else:
            vault.lock_now(home)
            vault.unlock(home, 'synthetic formal ownership password')
        assert not owner.is_live() and vault.is_unlocked(home)

    def verify(*_args):
        calls.append('checker')
        if phase == 'after-checker':
            revoke()
        return {'status': 'logic_verified', 'checkpoint': 'synthetic-only'}

    def review(*_args):
        calls.append('review')
        if phase == 'after-review':
            revoke()
        return {'status': 'reviewed', 'pending': {}}

    monkeypatch.setattr(mode, 'verify', verify)
    monkeypatch.setattr(mode, 'review', review)
    checkpoint = {'version': 1, 'atoms': {'P': 'the given premise'},
                  'assumptions': [{'id': 'a', 'statement': 'P', 'formula': 'P',
                                   'kind': 'given', 'quote': 'Assume P'}],
                  'hypotheses': [{'id': 'h', 'statement': 'P', 'formula': 'P', 'from': ['a']}],
                  'conclusion': 'h'}
    content = 'P holds.\n```hermes_logic\n'+json.dumps(checkpoint)+'\n```'
    try:
        with scoped_current_session_id('formal-A'):
            mode.begin_turn(agent, 'Assume P')
            if phase == 'before-checker':
                revoke()
            messages = []
            again, response = mode.gate_final(agent, {'role': 'assistant', 'content': content}, content, messages)
        assert calls == {'before-checker': [], 'after-checker': ['checker'],
                         'after-review': ['checker', 'review']}[phase]
        assert not again and not mode.state(agent).approved and not mode.state(agent).finalized
        assert not mode.state(agent).pending and messages == []
        assert 'Formal check: Lean' not in response
        if change == 'source-close':
            assert sibling.is_live()
        else:
            sibling.close()
            sibling = start_session_authority(home, 'formal-B-fresh')
        # Replacing the agent's ambient reference cannot renew an already-started review.
        agent._vault_session_authority = sibling
        assert mode.gate_final(agent, {}, content, [])[0] is False
        assert not mode.state(agent).approved and mode.state(agent).status == 'authority_ended'
        # A new turn under the independently valid sibling remains usable.
        monkeypatch.setattr(mode, 'verify', lambda *_: {'status': 'logic_verified', 'checkpoint': 'fresh'})
        monkeypatch.setattr(mode, 'review', lambda *_: {'status': 'reviewed', 'pending': {}})
        with scoped_current_session_id(sibling.session_id):
            mode.begin_turn(agent, 'Assume P')
            assert mode.gate_final(agent, {}, content, [])[0] is False
        assert mode.state(agent).approved and mode.state(agent).finalized and sibling.is_live()
    finally:
        owner.close()
        sibling.close()
        vault.clear_vault_cache()
