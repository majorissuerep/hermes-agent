"""Terminal UI and native headless entry contracts (no service transport)."""
from argparse import Namespace
import pytest


def test_interactive_default_and_query_dispatch(monkeypatch):
    from hermes_cli import main
    calls = []
    for name in ('_apply_safe_mode', '_apply_user_config_bypass', '_guard_noninteractive_user_config',
                 '_resolve_chat_session_args', '_refuse_unforwarded_tui_flags', '_pin_kanban_board_env'):
        monkeypatch.setattr(main, name, lambda *a, **k: None)
    monkeypatch.setattr(main, '_launch_tui', lambda *a, **k: calls.append(('tui', k)))
    monkeypatch.setattr(main, '_run_oneshot_from_args', lambda a: calls.append(('query', a.query)))
    args = Namespace(query=None, query_file=None, model=None)
    main.cmd_chat(args)
    assert calls[-1][0] == 'tui'
    args.query = 'A local terminal child task'
    main.cmd_chat(args)
    assert calls[-1] == ('query', args.query)


def test_removed_product_entries_are_not_parseable():
    from hermes_cli.main import _build_cli_parser
    parser, _ = _build_cli_parser()
    for argv in (['gateway'], ['serve'], ['desktop'], ['dashboard'], ['acp'], ['mcp', 'serve'], ['--cli'], ['chat', '--cli']):
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(argv)
        assert exc.value.code == 2
    assert parser.parse_args(['config', 'get', 'agent.max_turns']).command == 'config'


def test_headless_invokes_native_runner(monkeypatch):
    from hermes_cli import main
    calls = []
    monkeypatch.setattr(main, '_run_and_exit_oneshot', lambda *a, **k: calls.append((a, k)))
    main._run_oneshot_from_args(Namespace(query='Retain context', model='test/model', resume='saved-session'))
    assert calls == [(('Retain context',), {'model': 'test/model', 'provider': None, 'toolsets': None,
        'skills': None, 'usage_file': None, 'resume': 'saved-session', 'reasoning': None})]


def test_headless_holds_profile_until_cleanup(monkeypatch, tmp_path):
    from hermes_cli import oneshot
    from hermes_state.runtime_ownership import OwnershipConflict, exclusive_maintenance
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    def turn(*args):
        with pytest.raises(OwnershipConflict):
            with exclusive_maintenance([tmp_path]):
                pytest.fail('active terminal must fence maintenance')
        return 0
    monkeypatch.setattr(oneshot, '_run_owned_oneshot', turn)
    assert oneshot.run_oneshot('Local task') == 0
    with exclusive_maintenance([tmp_path]):
        pass


def test_native_headless_stream_keeps_protocol_outside_quiet_capture(monkeypatch, capsys):
    import json
    from hermes_cli import oneshot
    def turn(prompt, **kwargs):
        emitter = kwargs['launch_options']['emitter']
        emitter.on_text_delta('local streamed answer')
        print('internal tool chatter must stay quiet')
        return 'local streamed answer', {'final_response': 'local streamed answer', 'completed': True,
                                         'session_id': 'local-headless-test'}
    monkeypatch.setattr(oneshot, '_run_agent', turn)
    assert oneshot.run_oneshot('local task', launch_options={'output_format': 'stream-json'}) == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row['type'] for row in records] == ['system', 'text', 'result']
    assert records[-1]['text'] == 'local streamed answer'
    assert records[-1]['session_id'] == 'local-headless-test'


def test_native_route_failure_closes_acquired_session_store(monkeypatch):
    from hermes_cli import oneshot
    from hermes_cli.auth import AuthError
    closed = []
    class Store:
        def close(self):
            closed.append(True)
    monkeypatch.setattr('hermes_cli.config.load_config', lambda: {'model': {'default': 'test/model'}})
    monkeypatch.setattr(oneshot, '_create_session_db_for_oneshot', Store)
    def unavailable(*args, **kwargs):
        raise AuthError('No dedicated credential available')
    monkeypatch.setattr('hermes_cli.runtime_provider.resolve_runtime_with_fallback', unavailable)
    with pytest.raises(AuthError, match='dedicated credential'):
        oneshot._run_agent('Do not leak store on failed startup')
    assert closed == [True]


def test_headless_safe_mode_disables_context_at_agent_boundary(monkeypatch):
    from hermes_cli import main
    calls = []
    monkeypatch.setattr(main, "_run_and_exit_oneshot", lambda *a, **k: calls.append(k))
    main._run_oneshot_from_args(Namespace(query="isolated turn", safe_mode=True))
    assert calls[0]["launch_options"]["ignore_rules"] is True
