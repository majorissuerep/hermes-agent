"""Native activation reports real registrations without changing session prompt/tool pins."""


def test_native_commands_and_hooks_live_while_python_tools_and_prompt_defer(tmp_path, monkeypatch):
    from hermes_cli.plugins import LoadedPlugin, PluginContext, PluginManager, PluginManifest
    from hermes_cli.plugins_activation import plugin_activation_summary
    from tests.e2e.vault_home import seal_fixture_home

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    seal_fixture_home(tmp_path)
    manager = PluginManager()
    manifest = PluginManifest(name="native-activation", source="user")
    manager._plugins[manifest.name] = LoadedPlugin(manifest=manifest, enabled=True)
    context = PluginContext(manifest, manager)
    try:
        context.register_command("native-activation-command", lambda args: f"ran {args}")
        context.register_hook("pre_command", lambda **kwargs: "observed")
        context.register_hook("transform_llm_output", lambda **kwargs: "transformed")
        context.register_system_prompt_section("native-activation-section", "Frozen next session")
        context.register_tool(
            name="native_activation_tool", toolset="native-activation",
            schema={"name": "native_activation_tool", "description": "Test native registration",
                    "parameters": {"type": "object", "properties": {}}},
            handler=lambda args, **kwargs: "native result",
        )
        summary = plugin_activation_summary(manager, manifest.name)
        assert summary["activated_now"] == {
            "commands": ["native-activation-command"],
            "hooks": ["pre_command", "transform_llm_output"],
        }
        assert summary["deferred"] == {
            "tools": ["native_activation_tool"], "prompt": ["native-activation-section"],
        }
        assert manager._plugin_commands["native-activation-command"]["handler"]("args") == "ran args"
        assert manager.invoke_hook("pre_command", command="native-activation-command") == ["observed"]
    finally:
        manager.unload()


def test_retired_platform_manifest_is_refused_before_plugin_code_runs(tmp_path, caplog):
    from hermes_cli.plugins_manifest import parse_manifest_file

    plugin = tmp_path / "retired-platform"
    plugin.mkdir()
    manifest = plugin / "plugin.yaml"
    manifest.write_text("name: retired-platform\nkind: platform\n", encoding="utf-8")
    (plugin / "__init__.py").write_text("raise AssertionError('must not import')\n", encoding="utf-8")
    assert parse_manifest_file(manifest, plugin, "user", "") is None
    assert "Messaging platform plugins are not supported" in caplog.text
