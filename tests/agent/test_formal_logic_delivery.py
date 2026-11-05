"""Pure callback/protocol contracts; no compiler, provider or public application."""
import copy
from contextvars import copy_context
from types import SimpleNamespace

import pytest

from agent import formal_logic as mode
from agent.formal_logic_delivery import release_accepted
from agent.stream_delivery import StreamDeliveryMixin
from tests.agent.test_formal_logic import candidate


class DeliveryProbe(StreamDeliveryMixin):
    def __init__(self, *, enabled=True):
        self._formal_logic_state = mode.FormalState('lean', 1) if enabled else None
        self.display, self.speech, self.interim, self.reasoning, self.hooks = [], [], [], [], []
        self.stream_delta_callback, self._stream_callback = self.display.append, self.speech.append
        self.interim_assistant_callback = lambda text, **kw: self.interim.append(text)
        self.reasoning_callback = self.reasoning.append
        self.session_id, self.model, self.provider, self.platform = 'fixture', 'fixture', 'fixture', 'cli'
        from agent.agent_runtime_helpers import strip_think_blocks
        self._strip_think_blocks = lambda text: strip_think_blocks(None, text)


@pytest.fixture
def probe(monkeypatch):
    agent = DeliveryProbe()
    monkeypatch.setattr('agent.plugin_stream_hooks.enqueue_plugin_stream_hook',
                        lambda event, **fields: agent.hooks.append((event, fields)))
    return agent


def accept(agent, monkeypatch):
    monkeypatch.setattr(mode, 'verify', lambda *_: {'status': 'logic_verified', 'checkpoint': 'fixture'})
    monkeypatch.setattr(mode, 'review', lambda *_: {'status': 'reviewed'})
    raw = candidate()
    row = {'role': 'assistant', 'content': raw}
    again, clean = mode.gate_final(agent, row, raw, [])
    assert not again and '```hermes_logic' not in clean and 'Formal check:' in clean
    return raw, row, clean


@pytest.mark.parametrize('sink', ['stream', 'fanout', 'reasoning', 'commentary', 'interim', 'interim-fanout',
                                  'stream-end', 'suppressed-tool', 'scrubber-tail'])
def test_unchecked_prose_never_reaches_prose_sinks_or_stream_recovery(probe, sink):
    from agent.chat_completion_helpers import _StreamingCall

    raw = 'An unchecked conclusion. Please approve it now.'
    holder = SimpleNamespace(agent=probe, _quiet=lambda fn: fn())
    probe._stream_context_scrubber = SimpleNamespace(flush=lambda: raw)
    actions = {
        'stream': lambda: probe._fire_stream_delta(raw),
        'fanout': lambda: probe._deliver_to_stream_callbacks(raw),
        'reasoning': lambda: probe._fire_reasoning_delta(raw),
        'commentary': lambda: probe._fire_streamed_codex_commentary(raw),
        'interim': lambda: probe._emit_interim_assistant_message({'role': 'assistant', 'content': raw}),
        'interim-fanout': lambda: probe._deliver_interim(raw, already_streamed=False, record=[raw]),
        'stream-end': lambda: probe._emit_stream_end(final_text=raw, finished=True, error=None),
        'suppressed-tool': lambda: _StreamingCall._route_suppressed_text(holder, raw),
        'scrubber-tail': probe._reset_stream_delivery_tracking,
    }
    actions[sink]()
    assert not any((probe.display, probe.speech, probe.interim, probe.reasoning))
    assert raw not in repr(probe.hooks)
    assert not probe._current_streamed_assistant_text


def test_checked_final_releases_clean_display_and_speech_once(probe, monkeypatch):
    raw, row, clean = accept(probe, monkeypatch)
    probe._fire_stream_delta(raw)
    assert not probe.display and not probe.speech
    assert release_accepted(probe)
    assert not release_accepted(probe)
    assert probe.display == probe.speech == [clean]
    assert probe._current_streamed_assistant_text == clean
    assert row['api_content'] == raw  # Native model replay retains the exact checkpoint.
    probe._fire_stream_delta('A late unchecked candidate')
    assert probe.display == probe.speech == [clean]


def test_disabled_mode_retains_live_stream_interim_and_reasoning(monkeypatch):
    agent = DeliveryProbe(enabled=False)
    monkeypatch.setattr('agent.plugin_stream_hooks.enqueue_plugin_stream_hook', lambda *_a, **_k: None)
    agent._fire_stream_delta('ordinary live prose')
    agent._fire_reasoning_delta('ordinary reasoning')
    agent._emit_interim_assistant_message({'content': 'ordinary interim'})
    assert agent.display == agent.speech == ['ordinary live prose']
    assert agent.reasoning == ['ordinary reasoning'] and agent.interim == ['ordinary interim']
    assert release_accepted(agent) is False


def test_cancel_before_release_withholds_both_sinks(probe, monkeypatch):
    accept(probe, monkeypatch)
    probe._interrupt_requested = True
    assert not release_accepted(probe)
    assert not probe.display and not probe.speech
    assert not mode.state(probe).finalized


def test_callback_cannot_reenter_or_keep_a_delivery_context(probe, monkeypatch):
    _, _, clean = accept(probe, monkeypatch)
    contexts = []

    def display(text):
        probe.display.append(text)
        assert release_accepted(probe) is False
        contexts.append(copy_context())

    probe.stream_delta_callback = display
    assert release_accepted(probe)
    contexts[0].run(probe._fire_stream_delta, 'late copied-context prose')
    assert probe.display == probe.speech == [clean]


def test_explicit_bad_checkpoint_precedes_tool_approval_but_discovery_is_not_a_proof(monkeypatch):
    from agent import turn_response_intake as intake

    events, history = [], []
    agent = SimpleNamespace(_formal_logic_state=mode.FormalState('lean', 1))
    agent._build_assistant_message = lambda response, finish: {'role': 'assistant', 'content': response.content,
        'tool_calls': [{'id': call.id, 'function': {'name': call.function.name, 'arguments': '{}'}} for call in response.tool_calls]}
    agent._flush_messages_to_session_db = lambda *_: events.append('encrypted-native-flush')
    response = SimpleNamespace(content=candidate(), tool_calls=[SimpleNamespace(id='c', function=SimpleNamespace(name='terminal'))], finish_reason='tool_calls')
    monkeypatch.setattr(mode, 'verify', lambda *_: events.append('checker') or {'status': 'logic_failed'})
    monkeypatch.setattr(mode, 'review', lambda *_: pytest.fail('Review cannot precede proof'))
    monkeypatch.setattr(intake, 'normalize_response_for_agent', lambda *_: response)
    monkeypatch.setattr(intake, 'splice_provider_projection', lambda *_: None)
    monkeypatch.setattr(intake, '_fire_post_api_request_hook', lambda *_a, **_k: None)
    monkeypatch.setattr('hermes_cli.observability.shared_metrics_harness.record_reply_content', lambda *_: None)
    verdict = intake.normalize_model_response(agent, response=response, messages=history, api_messages=[],
        conversation_history=[], api_call_count=1, api_duration=0, api_start_time=0, api_request_id='r',
        effective_task_id='t', turn_id='turn')
    if verdict.action == 'fallthrough':
        events.append('approval-or-tool-effect')
    assert verdict.action == 'continue' and events == ['checker', 'encrypted-native-flush']
    assert [row['role'] for row in history] == ['assistant', 'tool']
    assert all(row.get('display_kind') == 'hidden' for row in history)
    response.content = 'I will list files.'
    assert mode.intermediate_gate(agent, response, [], []) is False
    assert mode.before_tool(agent, 'terminal', {'command': 'ls'}) is None
    assert mode.before_tool(agent, 'clarify', {'questions': [{'question': 'Please fix Lean'}]}) is not None


def test_hidden_repair_history_retains_native_replay_and_prior_prefix(probe, monkeypatch):
    from agent.message_metadata import without_persistence_fields
    from agent.turn_context import substitute_api_content
    from tui_runtime.server import _history_to_messages

    prefix = [{'role': 'user', 'content': 'Original immutable prompt'}]
    original = copy.deepcopy(prefix)
    monkeypatch.setattr(mode, 'verify', lambda *_: {'status': 'logic_failed'})
    raw = candidate()
    messages = list(prefix)
    assert mode.gate_final(probe, {'role': 'assistant', 'content': raw}, raw, messages)[0]
    assert _history_to_messages(messages) == [{'role': 'user', 'text': prefix[0]['content']}]
    assert messages[:1] == original
    wire = [dict(without_persistence_fields(row)) for row in messages]
    assert wire[1]['content'] == raw and 'display_kind' not in wire[1]
    _, accepted, clean = accept(probe, monkeypatch)
    accepted['reasoning'] = 'An unchecked private inference'
    accepted['codex_message_items'] = [{'type': 'message', 'role': 'assistant', 'phase': 'commentary',
                                      'content': [{'type': 'output_text', 'text': 'Unchecked provider detail'}]}]
    assert _history_to_messages([accepted]) == [{'role': 'assistant', 'text': clean,
                                               'display_metadata': {'formal_logic': 'reviewed'}}]
    replay = dict(without_persistence_fields(accepted))
    substitute_api_content(replay)
    assert replay['content'] == raw and accepted['content'] == clean
    assert messages[:1] == original


@pytest.mark.parametrize('change', ['source-close', 'vault-reunlock', 'home-retarget', 'cancel-during-display'])
def test_final_delivery_retains_original_source_and_never_borrows_sibling(tmp_path, monkeypatch, change):
    from hermes_security import vault
    from hermes_security.session_authority import start_session_authority

    home = tmp_path / 'owner'
    home.mkdir(mode=0o700)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(vault, '_SCRYPT_N', 1024)
    vault.init_vault(home, 'synthetic formal final delivery')
    vault.unlock(home, 'synthetic formal final delivery')
    first = start_session_authority(home, 'final-original')
    sibling = start_session_authority(home, 'final-sibling')
    agent = DeliveryProbe()
    mode.state(agent).authority = first
    monkeypatch.setattr('agent.plugin_stream_hooks.enqueue_plugin_stream_hook', lambda *_a, **_k: None)
    try:
        accept(agent, monkeypatch)
        if change == 'source-close':
            first.close()
            agent._vault_session_authority = sibling
        elif change == 'vault-reunlock':
            vault.lock_now(home)
            vault.unlock(home, 'synthetic formal final delivery')
            sibling.close()
            sibling = start_session_authority(home, 'fresh-sibling')
            agent._vault_session_authority = sibling
        elif change == 'home-retarget':
            other = tmp_path / 'other'
            other.mkdir(mode=0o700)
            monkeypatch.setenv('HERMES_HOME', str(other))
        else:
            def close_after_display(text):
                agent.display.append(text)
                first.close()
            agent.stream_delta_callback = close_after_display
        release_accepted(agent)
        assert not agent.speech
        assert bool(agent.display) == (change == 'cancel-during-display')
        assert sibling.is_live() and vault.is_unlocked(home)
        assert not mode.state(agent).finalized
        monkeypatch.setenv('HERMES_HOME', str(home))
        fresh = DeliveryProbe()
        mode.state(fresh).authority = sibling
        _, _, clean = accept(fresh, monkeypatch)
        assert release_accepted(fresh)
        assert fresh.display == fresh.speech == [clean]
        assert sibling.is_live()
    finally:
        first.close()
        sibling.close()
        vault.clear_vault_cache()


def test_actual_finalizer_releases_the_checked_answer_after_native_persist_step(monkeypatch):
    from agent.turn_finalizer import finalize_turn
    from tests.agent.test_turn_finalizer_final_response_persistence import FakeAgent

    class FinalProbe(DeliveryProbe, FakeAgent):
        def __init__(self):
            FakeAgent.__init__(self)
            DeliveryProbe.__init__(self)
            self._response_was_previewed = False

    agent = FinalProbe()
    monkeypatch.setattr('hermes_cli.plugins.invoke_hook', lambda *_a, **_k: [])
    monkeypatch.setattr('agent.plugin_stream_hooks.enqueue_plugin_stream_hook', lambda *_a, **_k: None)
    _, row, clean = accept(agent, monkeypatch)
    events = []
    original = agent._persist_session
    agent._persist_session = lambda *args: (events.append('persist'), original(*args))
    agent.stream_delta_callback = lambda text: (events.append('display'), agent.display.append(text))
    result = finalize_turn(agent, final_response=clean, api_call_count=1, interrupted=False,
        failed=False, messages=[{'role': 'user', 'content': 'Assume P'}, row], conversation_history=[],
        effective_task_id='task', turn_id='turn', user_message='Assume P', original_user_message='Assume P',
        _should_review_memory=False, _turn_exit_reason='text_response(finish_reason=stop)')
    assert events.index('persist') < events.index('display')
    assert result['final_response'] == clean and agent.display == agent.speech == [clean]
    assert mode.seal_result(agent, result)['last_reasoning'] is None


@pytest.mark.parametrize('nested', ['different-stream', 'same-stream', 'reasoning', 'interim', 'fanout'])
def test_accepted_callback_cannot_borrow_delivery_for_nested_prose(probe, monkeypatch, nested):
    _, _, clean = accept(probe, monkeypatch)
    actions = {
        'different-stream': lambda: probe._fire_stream_delta('Unchecked injected conclusion'),
        'same-stream': lambda: probe._fire_stream_delta(clean),
        'reasoning': lambda: probe._fire_reasoning_delta('Unchecked injected reasoning'),
        'interim': lambda: probe._emit_interim_assistant_message({'role': 'assistant', 'content': clean}),
        'fanout': lambda: probe._deliver_to_stream_callbacks(clean),
    }

    def display(text):
        probe.display.append(text)
        actions[nested]()

    probe.stream_delta_callback = display
    assert release_accepted(probe)
    assert probe.display == probe.speech == [clean]
    assert not probe.reasoning and not probe.interim
