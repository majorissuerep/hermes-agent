"""Immutable installation metadata becomes writable only in PM's staging copy."""

from pathlib import Path

import pytest

from pm import workspace


@pytest.mark.parametrize("replay", [False, True])
def test_readonly_manifest_is_rewritten_without_changing_input(tmp_path, replay):
    source = tmp_path / "source"
    source.mkdir()
    manifest = source / "pyproject.toml"
    original = '[project]\nname="fixture"\nversion="1.0"\n[tool.uv]\nexclude-newer="14 days"\n'
    manifest.write_text(original)
    manifest.chmod(0o444)
    (source / "uv.lock").write_text("version = 1\n")
    source.chmod(0o555)
    calls = []

    class Environment:
        def sync(self, root: Path, **kwargs):
            generated = root / "pyproject.toml"
            assert "exclude-newer" not in generated.read_text()
            assert generated.stat().st_mode & 0o200
            generated.write_text(generated.read_text() + "# engine write\n")
            calls.append(root)

    target = tmp_path / "staging"
    workspace.lock_and_sync([], [], root=target, source=source, seed_lock=None,
                            replay=source if replay else None, environment=Environment())
    assert calls == [target]
    assert manifest.read_text() == original
    assert manifest.stat().st_mode & 0o777 == 0o444
    assert source.stat().st_mode & 0o777 == 0o555


def test_readonly_virtual_plugin_metadata_is_generated_only_in_staging(tmp_path):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    original = '[project]\nname="virtual-fixture"\nversion="1.0"\n'
    manifest = plugin / "pyproject.toml"
    manifest.write_text(original)
    (plugin / "plugin.yaml").write_text("name: fixture\n")
    manifest.chmod(0o444)
    plugin.chmod(0o555)
    staging = tmp_path / "staging"
    staging.mkdir()
    member = workspace._workspace_member(plugin, staging, identity=plugin)
    assert "hermes-plugin-" in (member / "pyproject.toml").read_text()
    assert member.stat().st_mode & 0o200
    assert (member / "pyproject.toml").stat().st_mode & 0o200
    assert manifest.read_text() == original
    assert manifest.stat().st_mode & 0o777 == 0o444
    assert plugin.stat().st_mode & 0o777 == 0o555
