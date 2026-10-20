"""Transitive tool dependencies do not imply a requested Python command environment."""
import os
from types import SimpleNamespace

import pytest

from scripts.ci import setup_toolchain


@pytest.mark.parametrize("extras,commands_requested", [(["ripgrep"], False), (["ripgrep", "uv"], True)])
def test_node_extra_python_keeps_tool_environment_without_requiring_uv(tmp_path, monkeypatch, extras, commands_requested):
    from pm.registry import get_package

    names = setup_toolchain.packages("node", extras)
    assert "python" in names
    store = tmp_path / "tools"
    facts = {name: {"entry": name, "version": "1"} for name in names}
    ensured, built = [], []
    monkeypatch.setattr("pm.install.ensure", lambda name, **kw: ensured.append(name))
    monkeypatch.setattr("pm.lock.Facts", lambda path: SimpleNamespace(get=facts.get))
    monkeypatch.setattr("pm.paths.store_root", lambda: store)
    monkeypatch.setattr("pm.registry.get_package", lambda name: SimpleNamespace(
        name=name, deps=get_package(name).deps,
        binary=lambda entry, target: entry / "bin" / name))
    public = [name for name in names if name != "uv"]
    tool_env = {"PATH": os.pathsep.join(str(store / name / "bin") for name in public),
                "TOOL_DEPENDENCY_MARKER": "python-retained"}
    monkeypatch.setattr("pm.install.env_for", lambda *args, **kw: dict(tool_env))
    monkeypatch.setattr("pm.build_requirements_environment", lambda requirements, **kw: built.append((requirements, kw)))
    monkeypatch.setattr(setup_toolchain, "python3_alias", lambda python: None)

    def npm_cache(argv, **kwargs):
        assert argv == [str(store / "npm" / "bin" / "npm"), "config", "get", "cache"]
        assert kwargs["env"]["TOOL_DEPENDENCY_MARKER"] == "python-retained"
        return str(tmp_path / "npm-cache")

    monkeypatch.setattr("subprocess.check_output", npm_cache)
    files = {name: tmp_path / name for name in ("GITHUB_ENV", "GITHUB_OUTPUT", "GITHUB_PATH")}
    for name, path in files.items():
        monkeypatch.setenv(name, str(path))
    setup_toolchain.install(SimpleNamespace(toolchain="node", packages=extras, home=tmp_path / "home"))
    outputs = dict(line.split("=", 1) for line in files["GITHUB_OUTPUT"].read_text().splitlines())
    exported = dict(line.split("=", 1) for line in files["GITHUB_ENV"].read_text().splitlines())
    paths = files["GITHUB_PATH"].read_text().splitlines()
    assert ensured == ["npm", *extras]
    assert str(store / "python" / "bin") in paths
    assert outputs["ripgrep-path"] == str(store / "ripgrep" / "bin" / "ripgrep")
    assert bool(built) is commands_requested
    assert ("uv-path" in outputs) is commands_requested
    assert ("HERMES_PYTHON" in exported) is commands_requested
    if commands_requested:
        assert built[0][0] == [] and built[0][1]["python"] == store / "python" / "bin" / "python"
    else:
        assert outputs["python-path"] == str(store / "python" / "bin" / "python")
