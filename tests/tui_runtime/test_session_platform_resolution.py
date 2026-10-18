"""Native terminal defaults preserve explicit plugin session source tags."""


def test_standalone_runtime_defaults_to_tui():
    from tui_runtime import server

    assert server._resolve_session_platform() == "tui"
    assert server._resolve_session_source(None) == "tui"
    assert server._resolve_agent_platform(None) == "tui"


def test_explicit_plugin_source_is_preserved():
    from tui_runtime import server

    assert server._resolve_session_source("plugin-task") == "plugin-task"
    assert server._resolve_agent_platform("plugin-task") == "plugin-task"
    assert server._session_source({"source": "plugin-task"}) == "plugin-task"
