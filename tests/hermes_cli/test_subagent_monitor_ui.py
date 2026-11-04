"""Monitor navigation keeps controls pinned while live children finish."""
import asyncio
from types import SimpleNamespace


def test_monitor_keys_pin_controls_across_roster_changes(monkeypatch):
    from hermes_cli.cli_subagent_monitor import SubagentMonitor, build_monitor_application

    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from tools import delegate_tool_registry as registry
    monkeypatch.setattr(registry, '_active_subagents', {})
    received = []
    child = SimpleNamespace(steer=lambda text: received.append(text) or True,
                            interrupt=lambda *a, **kw: received.append('STOP') or True)
    for sid in ['first', 'second']:
        registry._register_subagent(dict(subagent_id=sid, goal=sid, owner_agent_session_id='owner',
            started_at=1, status='running', agent=child))

    dock = SubagentMonitor(SimpleNamespace(agent=SimpleNamespace(session_id='owner')))
    dock.refresh()

    async def run():
        with create_pipe_input() as pipe:
            output = DummyOutput()
            from prompt_toolkit.data_structures import Size
            output.get_size = lambda: Size(rows=14, columns=32)
            app = build_monitor_application(dock, input=pipe, output=output)
            footer = app.layout.container.children[-1].content.text()
            assert 'Ctrl+T' in footer and len(footer) <= 32
            rendered = asyncio.Event()
            app.after_render += lambda app: rendered.set()
            task = asyncio.create_task(app.run_async())
            await asyncio.wait_for(rendered.wait(), 3)

            async def send(text):
                rendered.clear()
                pipe.send_text(text)
                await asyncio.wait_for(rendered.wait(), 3)

            await send('\x1b[B\rsFocus on tests\r')
            await send('x')
            registry._active_subagents.pop('second')
            dock.refresh()
            await send('y')
            await send('\x1b')
            pipe.send_text('\x14')
            await asyncio.wait_for(task, 3)
    asyncio.run(run())
    assert dock.selected_id == 'first'
    assert received == ['Focus on tests']



def test_extended_tail_is_bounded_and_literal(tmp_path, monkeypatch):
    from hermes_cli.cli_subagent_monitor import read_tail
    from hermes_security import vault
    from hermes_security.delegation_state import DelegationStore

    home = tmp_path / 'profile'
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(vault, '_SCRYPT_N', 1024)
    vault.init_vault(home, 'synthetic monitor qualification password')
    vault.unlock(home, 'synthetic monitor qualification password')
    try:
        store = DelegationStore(home)
        path = home / 'cache' / 'delegation' / 'live' / 'deleg_tail' / 'task-0.log'
        store.create_log(path, 'old data\n' * 20000 + '\x1b[2Jlast activity\n')
        assert path.read_bytes().startswith(b'HRMVAULT\x00')
        tail = read_tail(str(path), store=store)
        assert 'last activity' in tail and '\x1b' not in tail
        assert len(tail) <= 32768
        assert 'not available' in read_tail(str(path.with_name('task-1.log')), store=store)
        raw = tmp_path / 'child.log'
        raw.write_text('plaintext must not become a trusted live view', encoding='utf-8')
        assert 'not available' in read_tail(str(raw), store=store)
    finally:
        vault.clear_vault_cache()
