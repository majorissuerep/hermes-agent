"""Source orchestration uses real node-deps/npm in an isolated checkout.

Only PM's tool acquisition (and its installed-node record) is substituted with the host's node/npm. Small
workspace scripts stand in for the expensive UI compilers; subprocess failures,
locked dependency selection, environment propagation and publication are real.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

import pm
from pm.package import Runner


def use_host_node_as_pm_node(monkeypatch):
    """Freshness reads run only under PM's recorded Node; stand the host's node in for it."""
    node, real = Path(shutil.which("node")), pm.installed_package
    monkeypatch.setattr(pm, "installed_package", lambda name, **kwargs: (
        SimpleNamespace(binary=node) if name == "node" else real(name, **kwargs)))


def copy_freshness_scripts(root):
    repository = Path(__file__).resolve().parents[2]
    scripts = root / "scripts/build"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in ("freshness.mjs", "frontend-common.mjs"):
        shutil.copy2(repository / "scripts/build" / name, scripts / name)


def stamp_product(root, product, out):
    script = (root / "scripts/build/freshness.mjs").as_uri()
    subprocess.run([shutil.which("node"), "--input-type=module", "-e",
                    f"import {{recordProduct, buildInputs}} from {json.dumps(script)};"
                    "const [source, product, out] = process.argv.slice(1);"
                    "recordProduct({source, product, out, inputs: buildInputs(source, product)});",
                    str(root), product, str(out)], check=True)


def test_source_build_uses_selected_python_for_isolated_icon_child(tmp_path, monkeypatch):
    from hermes_cli.source_build import source_build_env
    from pm import paths
    from pm.environments import site_packages, venv_python

    root = tmp_path / "source"
    root.mkdir()
    venv = root / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
    selected = site_packages(venv)
    (selected / "icon_dependency.py").write_text("ready = True\n", encoding="utf-8")
    monkeypatch.setattr(paths, "repo_root", lambda: root)
    monkeypatch.setattr(pm, "ensure", lambda name, **kwargs: Runner(name, kwargs["base_env"]))
    monkeypatch.syspath_prepend(str(selected))

    env = source_build_env()
    assert env["HERMES_PYTHON"] == str(venv_python(venv))
    subprocess.run([env["HERMES_PYTHON"], "-I", "-c",
                    "import icon_dependency; assert icon_dependency.ready"], check=True)


def test_automatic_build_preserves_pm_admission_intent(monkeypatch):
    from hermes_cli.source_build import source_build_env

    intent = []
    def acquire(name, *, base_env, explicit, verify=True):
        intent.append(explicit)
        return Runner(name, base_env)
    monkeypatch.setattr(pm, "ensure", acquire)
    source_build_env()
    assert intent == [False]


def test_installed_npm_does_not_authorize_missing_workspace_dependencies(source_checkout, monkeypatch):
    from hermes_cli.source_build import prepare_source_dependencies, source_build_env

    root, _ = source_checkout
    env = source_build_env()
    monkeypatch.setenv("HERMES_DISABLE_LAZY_INSTALLS", "1")
    with pytest.raises(subprocess.CalledProcessError):
        prepare_source_dependencies(root, ("ui-tui", "auxiliary"), env=env)
    assert _events(root) == []
    prepare_source_dependencies(root, ("ui-tui", "auxiliary"), env=env, explicit=True)
    events = _events(root)
    prepare_source_dependencies(root, ("ui-tui", "auxiliary"), env=env)
    assert _events(root) == events


@pytest.fixture
def source_checkout(tmp_path, monkeypatch):
    node, npm = shutil.which("node"), shutil.which("npm")
    assert node and npm, "source-build integration requires node and npm"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_RUNTIME_DIR", str(tmp_path / "tools"))
    monkeypatch.setenv("HERMES_DISABLE_LAZY_INSTALLS", "0")
    monkeypatch.setenv("npm_config_cache", str(tmp_path / "npm-cache"))
    monkeypatch.setenv("ESBUILD_BINARY_PATH", "/wrong/esbuild")
    monkeypatch.setenv("HERMES_PYTHON", "/wrong/python")
    (home / "npmrc").write_text("fund=false\n", encoding="utf-8")
    monkeypatch.delenv("NPM_CONFIG_USERCONFIG", raising=False)
    acquired = []

    def acquire(name, *, base_env=None, explicit=False, verify=True):
        acquired.append(name)
        assert name == "npm"

        return Runner(name, {**(base_env or os.environ), "PATH": os.pathsep.join(
            [str(Path(npm).parent), str(Path(node).parent), os.environ["PATH"]])})

    monkeypatch.setattr(pm, "ensure", acquire)
    use_host_node_as_pm_node(monkeypatch)
    root = tmp_path / "source with spaces"
    root.mkdir()
    workspaces = ["ui-tui", "auxiliary", "addon-fixture", "unrelated"]
    manifest = {"name": "build-fixture", "private": True, "version": "1.0.0",
                "workspaces": workspaces, "scripts": {"postinstall": "node log.mjs deps"}}
    (root / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    for workspace in workspaces:
        directory = root / workspace
        directory.mkdir(parents=True)
        (directory / "package.json").write_text(json.dumps({
            "name": workspace.replace("/", "-"), "version": "1.0.0",
            "scripts": {},
        }), encoding="utf-8")
    (root / "log.mjs").write_text(
        "import { appendFileSync } from 'node:fs';\n"
        "appendFileSync('events.jsonl', JSON.stringify({step: process.argv[2], "
        "python: process.env.HERMES_PYTHON, ci: process.env.CI, "
        "esbuild: process.env.ESBUILD_BINARY_PATH, "
        "npmrc: process.env.NPM_CONFIG_USERCONFIG}) + '\\n');\n",
        encoding="utf-8",
    )
    subprocess.run([npm, "install", "--package-lock-only", "--ignore-scripts", "--offline",
                    "--no-audit", "--no-fund"], cwd=root, check=True)
    scripts = root / "scripts" / "build"
    scripts.mkdir(parents=True)
    repository = Path(__file__).resolve().parents[2]
    for name in ("node-deps.mjs", "freshness.mjs", "frontend-common.mjs"):
        shutil.copy2(repository / "scripts/build" / name, scripts / name)
    (root / ".gitignore").write_text("node_modules/\n**/dist/\n", encoding="utf-8")
    return root, acquired


@pytest.fixture
def source_products(source_checkout, monkeypatch):
    root, acquired = source_checkout
    # Catalog migrations and optional SDK acquisition have their own contracts;
    # this real Node fixture owns only source build orchestration.
    monkeypatch.setattr("hermes_cli.main_install_repair._install_configured_features_missing_deps", lambda root: None)
    monkeypatch.setattr("hermes_cli.memory_provider_migration.migrate_all_homes", lambda: None)
    monkeypatch.setattr("hermes_cli.left_core_migration.migrate_all_homes", lambda: None)
    (root / "product.mjs").write_text(
        "import { appendFileSync, existsSync, mkdirSync, writeFileSync } from 'node:fs';\n"
        "import { dirname, join } from 'node:path';\n"
        "import { fileURLToPath } from 'node:url';\n"
        "import { recordProduct, buildInputs } from './scripts/build/freshness.mjs';\n"
        "const root = dirname(fileURLToPath(import.meta.url));\n"
        "appendFileSync(join(root, 'events.jsonl'), JSON.stringify({step: 'tui'}) + '\\n');\n"
        "if (existsSync(join(root, 'fail-tui'))) throw new Error('fixture tui failure');\n"
        "const out = join(root, 'ui-tui/dist'); mkdirSync(out, {recursive: true});\n"
        "writeFileSync(join(out, 'entry.js'), 'tui');\n"
        "recordProduct({source: root, product: 'tui', out, inputs: buildInputs(root, 'tui')});\n",
        encoding="utf-8",
    )
    (root / "scripts/build/tui.mjs").write_text("import '../../product.mjs';\n", encoding="utf-8")
    return root, acquired


def _events(root):
    path = root / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.platforms("posix")
def test_preparation_reuses_only_the_exact_completed_workspace_union(source_checkout):
    from hermes_cli.source_build import prepare_source_dependencies, source_build_env

    root, acquired = source_checkout
    before = (root / "package-lock.json").read_bytes()
    env = source_build_env()
    prepare_source_dependencies(root, ("ui-tui", "auxiliary"), env=env)
    first = _events(root)
    assert [event["step"] for event in first] == ["deps"]
    assert first[0]["python"] == env["HERMES_PYTHON"]
    assert first[0]["ci"] == "1"
    assert "esbuild" not in first[0]
    assert first[0]["npmrc"] == str(Path(os.environ["HERMES_HOME"]) / "npmrc")
    assert (root / "node_modules/ui-tui").exists()
    assert (root / "node_modules/auxiliary").exists()
    assert not (root / "node_modules/addon-fixture").exists()
    assert not (root / "node_modules/unrelated").exists()

    prepare_source_dependencies(root, ("ui-tui", "auxiliary"), env=env)
    assert _events(root) == first
    prepare_source_dependencies(root, ("ui-tui", "auxiliary", "addon-fixture"), env=env)
    assert len(_events(root)) == 2
    assert (root / "node_modules/addon-fixture").exists()
    assert not (root / "node_modules/unrelated").exists()
    assert (root / "package-lock.json").read_bytes() == before
    assert acquired == ["npm"]

    # A lock failure must not fall back to npm install and rewrite the lock.
    (root / "package-lock.json").write_text("not json", encoding="utf-8")
    with pytest.raises(subprocess.CalledProcessError):
        prepare_source_dependencies(root, ("ui-tui", "auxiliary"), env=env)
    assert (root / "package-lock.json").read_text() == "not json"
    assert len(_events(root)) == 2
    assert acquired == ["npm"]

    # Exercise PM rather than hiding a provisioning error behind system npm.
    from unittest.mock import patch
    with patch.object(pm, "ensure", side_effect=pm.InstallError("npm", "unavailable")):
        with pytest.raises(pm.InstallError, match="unavailable"):
            source_build_env()
    assert len(_events(root)) == 2


@pytest.mark.platforms("posix")
def test_update_builds_native_tui_after_bounded_dependency_preparation(source_products):
    from hermes_cli.source_build import build_update_products

    root, acquired = source_products
    build_update_products(root)
    assert [event["step"] for event in _events(root)] == ["deps", "tui"]
    assert acquired == ["npm"]
    assert (root / "ui-tui/dist/entry.js").read_text() == "tui"
    assert not (root / "node_modules/addon-fixture").exists()
    assert not (root / "node_modules/unrelated").exists()


@pytest.mark.platforms("posix")
def test_update_recompiles_only_when_native_inputs_changed(source_products):
    from hermes_cli.source_build import build_update_products

    root, _ = source_products
    build_update_products(root)
    (root / "events.jsonl").unlink()
    build_update_products(root)
    assert _events(root) == []
    (root / "ui-tui/src").mkdir()
    (root / "ui-tui/src/changed.ts").write_text("export {}\n", encoding="utf-8")
    build_update_products(root)
    assert [event["step"] for event in _events(root)] == ["tui"]


@pytest.mark.platforms("posix")
def test_native_build_failure_is_reported_once_without_retry(source_products):
    from hermes_cli.source_build import ProductBuildError, build_update_products

    root, acquired = source_products
    (root / "fail-tui").touch()
    with pytest.raises(ProductBuildError) as failure:
        build_update_products(root)
    assert [name for name, _ in failure.value.failures] == ["TUI build"]
    assert isinstance(failure.value.failures[0][1], subprocess.CalledProcessError)
    assert [event["step"] for event in _events(root)] == ["deps", "tui"]
    assert acquired == ["npm"]
    assert not (root / "ui-tui/dist/entry.js").exists()


@pytest.mark.platforms("posix")
def test_feature_failure_does_not_skip_independent_native_build(source_products, monkeypatch):
    from hermes_cli.source_build import ProductBuildError, build_update_products

    root, _ = source_products
    def unavailable(root):
        raise RuntimeError("configured SDK unavailable")
    monkeypatch.setattr("hermes_cli.main_install_repair._install_configured_features_missing_deps", unavailable)
    with pytest.raises(ProductBuildError) as failure:
        build_update_products(root)
    assert [name for name, _ in failure.value.failures] == ["feature dependencies"]
    assert [event["step"] for event in _events(root)] == ["deps", "tui"]
    assert (root / "ui-tui/dist/entry.js").is_file()


@pytest.mark.platforms("posix")
def test_module_cli_builds_native_product(source_products, monkeypatch):
    import runpy

    root, acquired = source_products
    monkeypatch.setattr(sys, "argv", ["source_build", "--source", str(root)])
    monkeypatch.delitem(sys.modules, "hermes_cli.source_build", raising=False)
    runpy.run_module("hermes_cli.source_build", run_name="__main__")
    assert acquired == ["npm"]
    assert (root / "ui-tui/dist/entry.js").is_file()
