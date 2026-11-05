"""Display-only formal metadata preserves replay bytes and useful ordinary tool cards."""
import copy
import json
from types import SimpleNamespace

import pytest

from agent import formal_logic as mode
from agent.formal_logic_delivery import mark_candidate
from agent.message_metadata import without_persistence_fields


def test_unchecked_tool_prose_is_hidden_without_losing_real_tool_card_or_prefix():
    from tui_runtime.server import _history_to_messages

    prefix = [{'role': 'user', 'content': 'Existing prompt'},
              {'role': 'assistant', 'content': 'Existing accepted reply', 'reasoning': 'Existing detail'}]
    original_prefix = copy.deepcopy(prefix)
    agent = SimpleNamespace(_formal_logic_state=mode.FormalState('lean', 1))
    candidate = {'role': 'assistant', 'content': 'An unchecked inference. Please approve it.',
                 'tool_calls': [{'id': 'read1', 'type': 'function',
                                 'function': {'name': 'read_file', 'arguments': json.dumps({'path': '/public/example'})}}]}
    replay_before = copy.deepcopy(candidate)
    mark_candidate(agent, candidate)
    history = [*prefix, candidate, {'role': 'tool', 'tool_call_id': 'read1', 'content': 'Public tool result'}]
    snapshot = copy.deepcopy(history)
    projected = _history_to_messages(history)
    assert all('unchecked inference' not in row.get('text', '') for row in projected)
    tools = [row for row in projected if row['role'] == 'tool']
    assert len(tools) == 1 and tools[0]['name'] == 'read_file'
    assert tools[0]['args'] == {'path': '/public/example'} and tools[0]['tool_call_id'] == 'read1'
    assert dict(without_persistence_fields(candidate)) == replay_before
    assert prefix == original_prefix and history == snapshot


def test_disabled_projection_and_metadata_are_unchanged():
    from tui_runtime.server import _history_to_messages

    agent = SimpleNamespace(_formal_logic_state=None)
    row = {'role': 'assistant', 'content': 'Ordinary commentary', 'reasoning': 'Ordinary detail'}
    before = copy.deepcopy(row)
    mark_candidate(agent, row)
    assert row == before
    assert _history_to_messages([row]) == [{'role': 'assistant', 'text': row['content'], 'reasoning': row['reasoning']}]


def test_unrelated_hidden_scaffolding_keeps_its_existing_projection():
    from tui_runtime.server import _history_to_messages

    history = [{'role': 'assistant', 'display_kind': 'hidden', 'content': 'Existing hidden content',
                'tool_calls': [{'id': 'hidden1', 'function': {'name': 'read_file',
                                                            'arguments': '{"path":"existing-hidden-path"}'}}]},
               {'role': 'tool', 'tool_call_id': 'hidden1', 'content': 'Existing tool result'}]
    projected = _history_to_messages(history)
    assert len(projected) == 1 and projected[0]['role'] == 'tool'
    assert projected[0]['name'] == 'tool' and 'args' not in projected[0]


@pytest.mark.parametrize('enabled', [False, True])
def test_real_prompt_callbacks_do_not_queue_unchecked_speech_or_inflight_text(monkeypatch, enabled):
    import contextlib
    from tui_runtime import server
    from tests.agent.test_formal_logic_delivery import DeliveryProbe, accept
    from agent.formal_logic_delivery import release_accepted

    events, speech = [], []
    agent = DeliveryProbe(enabled=enabled)
    session = {'history_lock': contextlib.nullcontext(), 'session_key': 'fixture'}
    st = SimpleNamespace(agent=agent, history=[], tts_queue=SimpleNamespace(put=speech.append))
    monkeypatch.setattr(server, '_emit', lambda event, sid, payload: events.append((event, payload)))
    monkeypatch.setattr(server, '_load_interim_assistant_messages', lambda: True)
    monkeypatch.setattr(server, '_adopt_submit_user_row', lambda *_a, **_k: None)
    monkeypatch.setattr(server, '_start_usage_ticker', lambda *_a: (
        SimpleNamespace(set=lambda: None), SimpleNamespace(join=lambda: None)))
    monkeypatch.setattr('agent.notification_presentation.notification_turn', lambda *_a, **_k: contextlib.nullcontext())
    monkeypatch.setattr('agent.notification_presentation.event_presentation_muted', lambda *_a: False)
    monkeypatch.setattr('agent.plugin_stream_hooks.enqueue_plugin_stream_hook', lambda *_a, **_k: None)
    raw = 'Unchecked candidate. Please approve my proof.'
    expected = []

    def run(_message, *, stream_callback, **_kwargs):
        agent._stream_callback = stream_callback
        stream_callback(raw)  # Exercise the real TUI receiver, including the TTS queue.
        agent.interim_assistant_callback(raw)
        if enabled:
            _, _, clean = accept(agent, monkeypatch)
            expected.append(clean)
            assert release_accepted(agent)
            assert not release_accepted(agent)
            return {'final_response': clean}
        expected.append(raw)
        return {'final_response': raw}

    agent.run_conversation = run
    server._invoke_agent('fixture', session, st, 'Assume P', 'Assume P', None, [], None, None)
    assert speech == expected
    assert [payload['text'] for event, payload in events if event == 'message.delta'] == expected
    assert session['inflight_turn']['assistant'] == expected[0]
    interim = [payload['text'] for event, payload in events if event == 'message.interim']
    assert interim == ([] if enabled else [raw])


@pytest.mark.parametrize('accepted', [False, True])
def test_encrypted_reload_keeps_raw_replay_and_only_checked_or_failure_presentation(tmp_path, monkeypatch, accepted):
    from agent import turn_stop_gates as gates
    from agent.turn_context import substitute_api_content
    from hermes_security import vault
    from hermes_state import SessionDB
    from tests.agent.test_formal_logic import candidate
    from tests.agent.test_formal_logic_delivery import DeliveryProbe, accept
    from tui_runtime.server import _history_to_messages

    home = tmp_path / 'owner'
    home.mkdir(mode=0o700)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(vault, '_SCRYPT_N', 1024)
    vault.init_vault(home, 'synthetic formal reload')
    vault.unlock(home, 'synthetic formal reload')
    agent = DeliveryProbe()
    raw = candidate()
    prefix = {'role': 'user', 'content': 'Original immutable prompt'}
    original = copy.deepcopy(prefix)
    retry = mark_candidate(agent, {'role': 'assistant', 'content': raw})
    feedback = mark_candidate(agent, {'role': 'user', 'content': 'Repair automatically',
                                      '_formal_logic_feedback': True})
    row = {'role': 'assistant', 'content': '' if accepted else raw,
           'reasoning': 'Unchecked private sidecar',
           'codex_message_items': [{'type': 'message', 'role': 'assistant', 'phase': 'commentary',
                                   'content': [{'type': 'output_text', 'text': 'Unchecked private sidecar'}]}]}
    if accepted:
        accept(agent, monkeypatch)
        row['api_content'] = raw
    else:
        mode.state(agent).attempts = 1
        monkeypatch.setattr(mode, 'verify', lambda *_: {'status': 'logic_failed'})
    for seam in ('_verify_on_stop_nudge', '_pre_verify_nudge', '_kanban_stop_nudge'):
        monkeypatch.setattr(gates, seam, lambda *_: None)
    verdict = gates.apply_stop_gates(agent, row, final_response=raw, messages=[], conversation_history=[],
        pending_verification_response=None, pending_verification_response_previewed=False)
    assert not verdict.continue_turn
    assert ('Formal check:' if accepted else 'No verified conclusion') in verdict.final_response
    try:
        with SessionDB(db_path=home / 'history.db') as db:
            db.create_session('formal-reload', source='cli')
            db.append_messages_batch('formal-reload', copy.deepcopy([prefix, retry, feedback, row]))
        with SessionDB(db_path=home / 'history.db') as db:
            restored = db.get_messages_as_conversation('formal-reload')
        projected = _history_to_messages(restored)
        assert len(projected) == 2 and projected[-1]['text'] == verdict.final_response
        assert restored[1]['display_kind'] == restored[2]['display_kind'] == 'hidden'
        assert restored[1]['display_metadata'] == {'formal_logic': 'candidate'}
        assert restored[-1]['display_metadata'] == {'formal_logic': 'reviewed' if accepted else 'unverified'}
        assert 'Unchecked private sidecar' not in repr(projected)
        assert restored[0]['content'] == original['content'] and prefix == original
        replay = dict(without_persistence_fields(restored[-1]))
        substitute_api_content(replay)
        assert replay['content'] == raw
        assert raw.encode() not in (home / 'history.db').read_bytes()
    finally:
        vault.clear_vault_cache()
