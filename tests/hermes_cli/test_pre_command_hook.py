"""Native slash-command pre_command hooks observe dispatch without vetoing it."""



# ---------------------------------------------------------------------------
# Hook registration
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# fire_pre_command_hook helper
# ---------------------------------------------------------------------------


def test_fire_helper_is_observer_only_and_never_raises(monkeypatch):
    """Directive-shaped returns are ignored; plugin exceptions don't escape."""
    from hermes_cli import plugins as plugins_mod

    calls = {}

    class _FakeManager:
        def has_hook(self, name):
            return name == "pre_command"

        def invoke_hook(self, name, **kwargs):
            calls["name"] = name
            calls["kwargs"] = kwargs
            # A directive-shaped return must be ignored (observer v1).
            return [{"action": "block", "message": "nope"}]

    monkeypatch.setattr(plugins_mod, "get_plugin_manager", _FakeManager)

    # Must not raise, must not return anything actionable.
    result = plugins_mod.fire_pre_command_hook(
        surface="cli",
        command="model",
        alias_used="model",
        args_raw="gpt-x",
    )
    assert result is None
    assert calls["name"] == "pre_command"
    assert calls["kwargs"]["surface"] == "cli"
    assert calls["kwargs"]["command"] == "model"




def test_fire_helper_swallows_manager_errors(monkeypatch):
    from hermes_cli import plugins as plugins_mod

    def _boom():
        raise RuntimeError("plugin discovery exploded")

    monkeypatch.setattr(plugins_mod, "get_plugin_manager", _boom)
    # Never raises.
    plugins_mod.fire_pre_command_hook(
        surface="cli", command="new", alias_used="reset", args_raw="",
    )


# ---------------------------------------------------------------------------
# CLI surface (cli.py process_command)
# ---------------------------------------------------------------------------


def _make_cli():
    import cli as cli_mod

    inst = object.__new__(cli_mod.HermesCLI)
    inst.session_id = "sess-cli-1"
    inst._pending_resume_sessions = None
    return inst


def test_cli_fires_for_recognized_command(monkeypatch):
    from hermes_cli import plugins as plugins_mod

    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(plugins_mod, "fire_pre_command_hook", _capture)

    inst = _make_cli()
    inst.show_help = lambda *a, **k: None
    assert inst.process_command("/help") is True

    assert captured["surface"] == "cli"
    assert captured["command"] == "help"
    assert captured["alias_used"] == "help"
    assert captured["args_raw"] == ""
    assert captured["session_key"] == "sess-cli-1"


def test_cli_reports_canonical_name_for_alias(monkeypatch):
    """/exit is an alias of /quit — hook payload reports canonical 'quit'."""
    from hermes_cli import plugins as plugins_mod

    captured = {}
    monkeypatch.setattr(
        plugins_mod, "fire_pre_command_hook",
        lambda **kwargs: captured.update(kwargs),
    )

    inst = _make_cli()
    # /exit returns False (exit the REPL) — dispatch still happens after
    # the hook fires.
    assert inst.process_command("/exit") is False

    assert captured["command"] == "quit"
    assert captured["alias_used"] == "exit"
    assert captured["surface"] == "cli"


def test_cli_passes_raw_args(monkeypatch):
    from hermes_cli import plugins as plugins_mod

    captured = {}
    monkeypatch.setattr(
        plugins_mod, "fire_pre_command_hook",
        lambda **kwargs: captured.update(kwargs),
    )

    inst = _make_cli()
    inst.show_help = lambda *a, **k: None
    # /help ignores args but the payload must carry them raw (case kept).
    inst.process_command("/help Some RAW args")
    assert captured["args_raw"] == "Some RAW args"


def test_cli_hook_before_handler(monkeypatch):
    """The hook fires BEFORE the command handler runs."""
    from hermes_cli import plugins as plugins_mod

    order = []
    monkeypatch.setattr(
        plugins_mod, "fire_pre_command_hook",
        lambda **kwargs: order.append("hook"),
    )

    inst = _make_cli()
    inst.show_help = lambda *a, **k: order.append("handler")
    inst.process_command("/help")
    assert order == ["hook", "handler"]


# ---------------------------------------------------------------------------
# Gateway surface (gateway/run.py _handle_message)
# ---------------------------------------------------------------------------
