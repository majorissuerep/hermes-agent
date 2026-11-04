"""One-turn model picks deferred during a streamed reply belong to the next turn."""
from types import SimpleNamespace

import pytest

from tui_runtime import server


class _Prepared(Exception):
    """Stop at the model-ready boundary without starting any application/model call."""


class _Agent:
    model = 'fixture-gamma'
    provider = 'custom:fixture'
    api_key = 'synthetic-key'
    base_url = 'http://fixture.invalid/v1'
    api_mode = 'chat_completions'
    reasoning_config = {'effort': 'none'}
    _primary_runtime = None

    def switch_model(self, **values):
        for field, name in [('model', 'new_model'), ('provider', 'new_provider'), ('api_key', 'api_key'),
                            ('base_url', 'base_url'), ('api_mode', 'api_mode')]:
            setattr(self, field, values[name])
        self.reasoning_config = {'effort': 'none'}


@pytest.mark.parametrize('deferred', [False, True], ids=['idle-pick', 'pick-during-turn-settlement'])
def test_once_model_and_effort_apply_to_exactly_one_prepared_turn(monkeypatch, deferred):
    from hermes_cli import config, model_switch
    from hermes_cli.observability import shared_metrics_events
    from tools import approval_context

    agent = _Agent()
    session = {'agent': agent, 'session_key': 'synthetic-model-session',
               'model_override': {'model':'fixture-gamma', 'provider':'custom:fixture'}}
    result = SimpleNamespace(success=True, new_model='fixture-beta', target_provider='custom:fixture',
        api_key=agent.api_key, base_url=agent.base_url, api_mode=agent.api_mode, warning_message='')
    monkeypatch.setattr(model_switch, 'switch_model', lambda **_: result)
    monkeypatch.setattr(config, 'load_config', dict)
    monkeypatch.setattr(shared_metrics_events, 'record_model_switch', lambda **_: None)
    for name in ('_restart_slash_worker', '_persist_live_session_runtime', '_persist_live_session_system_prompt',
                 '_append_model_switch_marker', '_emit_session_info', '_wire_callbacks',
                 '_sync_agent_model_with_config', '_sync_agent_compression_with_config',
                 '_sync_agent_fallback_with_config', '_merge_preflight_warning'):
        monkeypatch.setattr(server, name, lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, '_expensive_model_confirm', lambda *_: None)
    monkeypatch.setattr(server, '_profile_runtime_scope_tokens', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, '_set_session_context', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(approval_context, 'set_current_session_key', lambda *_: None)
    def prepared(_session):
        raise _Prepared
    monkeypatch.setattr(server, '_adopt_out_of_band_turns', prepared)

    raw = 'fixture-beta --once --reasoning high'
    if deferred:
        session['pending_model_switch'] = {'raw':raw, 'display_model':'fixture-beta'}
    else:
        server._apply_model_switch('sid', session, raw)
    observed = []
    for _ in range(2):
        turn = server._TurnRun(agent, session.pop('one_turn_model_restore', None))
        with pytest.raises(_Prepared):
            server._prepare_turn_input('sid', session, turn, 'synthetic prompt', [])
        observed.append((agent.model, agent.reasoning_config['effort']))
        server._restore_agent_model_runtime(agent, turn.one_turn_restore)
    assert observed == [('fixture-beta', 'high'), ('fixture-gamma', 'none')]
    assert agent.model == 'fixture-gamma' and agent.reasoning_config['effort'] == 'none'
    assert 'one_turn_model_restore' not in session and 'pending_model_switch' not in session
    assert session['model_override']['model'] == 'fixture-gamma'
