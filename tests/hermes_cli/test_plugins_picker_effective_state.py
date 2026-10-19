"""The native plugins picker writes only user-flipped rows and preserves unrelated selections."""
import pytest
import hermes_yaml as yaml

from tests.hermes_cli.plugin_worker_support import (
    plugin_world as plugin_world,  # noqa: PLC0414 -- the self-alias is load-bearing: it suppresses F811 for the pytest fixture parameter shadowing this import
    isolated_python as isolated_python,  # noqa: PLC0414 -- the self-alias is load-bearing: it suppresses F811 for the pytest fixture parameter shadowing this import
)


@pytest.fixture
def run_picker(plugin_world, monkeypatch):
    from hermes_cli import plugins_cmd

    world = plugin_world
    for leaf in ("first", "second"):
        platform = world.core / "plugins" / "tools" / leaf
        platform.mkdir(parents=True)
        (platform / "plugin.yaml").write_text(f"name: {leaf}-tool\nkind: standalone\n", encoding="utf-8")
    monkeypatch.setattr("hermes_cli.plugins.get_bundled_plugins_dir", lambda: world.core / "plugins")
    monkeypatch.setattr(plugins_cmd, "_provider_categories", list)
    monkeypatch.setattr(plugins_cmd.sys.stdin, "isatty", lambda: True)
    monkeypatch.setitem(plugins_cmd.sys.modules, "curses", None)
    config_path = world.home / "config.yaml"

    def _run(plugins: dict, toggle_keys=()):
        config_path.write_text(yaml.safe_dump({"plugins": plugins}), encoding="utf-8")
        keys = [entry[5] for entry in plugins_cmd._discover_all_plugins()]
        answers = iter([str(keys.index(k) + 1) for k in toggle_keys] + [""])
        monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
        plugins_cmd.cmd_toggle()
        return yaml.safe_load(config_path.read_text(encoding="utf-8"))["plugins"]

    return _run


def test_open_and_exit_without_changes_writes_nothing(run_picker):
    assert run_picker({"enabled": [], "disabled": []}) == {"enabled": [], "disabled": []}


def test_only_flipped_rows_are_written(run_picker):
    # The first plugin is disabled under both canonical key and manifest alias.
    # Enable it and disable the second; preserve a selected but unshown plugin.
    plugins = run_picker(
        {"enabled": ["tools/second", "some/unshown-plugin"], "disabled": ["tools/first", "first-tool"]},
        toggle_keys=("tools/first", "tools/second"),
    )
    assert plugins["disabled"] == ["tools/second"]
    assert sorted(plugins["enabled"]) == ["tools/first", "some/unshown-plugin"]
