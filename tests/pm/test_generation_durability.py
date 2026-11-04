"""A generation's selection record is its completion marker, so it may only be
written after the finished tree has been flushed to disk. Otherwise a power loss
mid-publish can persist the (fsynced) record while the venv's files are still in
the page cache, and the next boot selects a half-written environment.
"""
from __future__ import annotations

import errno
import os

import pytest


def _flush_probe(monkeypatch, observe, *, fail=False):
    from pm import filesystem

    seen = []
    def flush(*directories):
        assert directories, "publication must identify the completed generation"
        seen.append((tuple(directory.resolve() for directory in directories), observe()))
        if fail:
            raise OSError(errno.EIO, "synthetic writeback failure")
    def global_flush():
        raise AssertionError("publication must not flush unrelated filesystems")
    monkeypatch.setattr(os, "sync", global_flush, raising=False)
    monkeypatch.setattr(filesystem, "sync_filesystems", flush, raising=False)
    return seen


@pytest.mark.parametrize("flush_fails", [False, True], ids=["durable", "writeback-error"])
def test_install_selection_is_committed_only_after_its_tree_is_flushed(tmp_path, monkeypatch, flush_fails):
    from pm import install
    from pm import paths
    from pm.environments import install_state_dir, runtime_facts_path, selected_venv, venv_python

    store = tmp_path / "tools"
    store.mkdir()
    monkeypatch.setattr("pm.paths.store_root", lambda: store)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(paths, "repo_root", lambda: repo)
    venv = install.get_package("venv")
    monkeypatch.setattr(venv, "expected_stamp", lambda extras, **kwargs: "stamp")
    candidate = install_state_dir(repo) / "environments" / "fresh" / "venv"

    def apply(*args, **kwargs):
        candidate.mkdir(parents=True)
        (candidate / "pyvenv.cfg").write_text("home = fixture\n", encoding="utf-8")
        executable = venv_python(candidate)
        executable.parent.mkdir()
        executable.touch()
        return {"environment": candidate}

    monkeypatch.setattr(venv, "apply", apply)

    def committed() -> bool:
        return runtime_facts_path(repo).is_file() and selected_venv(repo) == candidate.resolve()

    flushes = _flush_probe(monkeypatch, lambda: (candidate / "pyvenv.cfg").is_file() and not committed(),
                           fail=flush_fails)
    if flush_fails:
        with pytest.raises(OSError, match="writeback failure"):
            install.sync_venv([], explicit=True)
        assert not runtime_facts_path(repo).is_file()
    else:
        install.sync_venv([], explicit=True)
        assert selected_venv(repo) == candidate.resolve()
    assert flushes == [((candidate.resolve(), venv_python(candidate).resolve().parent), True)]


def test_side_environment_selection_is_committed_only_after_its_tree_is_flushed(tmp_path, monkeypatch):
    from pm import _uv, operations

    base = tmp_path / "base" / "python"
    base.parent.mkdir()
    base.touch()
    monkeypatch.setattr(_uv, "_toolchain", lambda **kwargs: (None, base))
    root = tmp_path / "side"
    built: list = []

    def build(generation, base_python):
        environment = generation / "venv"
        python = operations._python(environment)
        python.parent.mkdir(parents=True)
        python.touch()
        (environment / "pyvenv.cfg").write_text("home = fixture\n", encoding="utf-8")
        built.append(environment)
        return python

    flushes = _flush_probe(monkeypatch, lambda: bool(built) and not (root / "active.json").exists())
    python = operations._ensure_generation("proof", root, {}, build, record={}, explicit=True)

    assert flushes == [((built[0].parent.resolve(), base.parent.resolve()), True)]
    assert operations.environment_python("proof", root=root) == python
