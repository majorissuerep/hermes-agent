"""CI namespace authorization owns its exact profile and retains the worker's policy."""
from __future__ import annotations

import subprocess

import pytest

from scripts.ci import native_namespace_policy as policy


@pytest.fixture
def namespace_worker(tmp_path, monkeypatch):
    restriction = tmp_path / "restriction"
    restriction.write_bytes(b"1\n")
    identity = {"uid": 10001, "euid": 10001, "gid": 10001, "egid": 10001, "groups": [10001]}
    monkeypatch.setattr(policy, "_RESTRICTION", restriction)
    monkeypatch.setattr(policy, "_identity", lambda: identity.copy())
    monkeypatch.setattr(policy, "_label", lambda: "unconfined")
    monkeypatch.setattr(policy, "_command", lambda name: [f"/native/{name}"])
    monkeypatch.setattr(policy.shutil, "copyfile", lambda source, target: target.write_bytes(b"public launcher"))
    return restriction


def test_failed_qualification_removes_only_its_added_profile(namespace_worker, monkeypatch):
    calls = []

    def parser(command, **options):
        calls.append((command, options["input"]))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(policy.subprocess, "run", parser)
    with pytest.raises(RuntimeError, match="qualification failed"):
        with policy._authorized_launcher(env={}) as (launcher, env):
            assert launcher.is_file()
            assert policy._CONTEXT in env
            # A child-controlled file cannot change the parent's removal definition.
            (launcher.parent / "profile").write_text("unrelated existing policy")
            raise RuntimeError("qualification failed")
    assert not launcher.parent.exists()
    assert len(calls) == 2 and "--add" in calls[0][0] and "--remove" in calls[1][0]
    assert calls[0][1] == calls[1][1]
    assert namespace_worker.read_bytes() == b"1\n"


def test_failed_profile_add_does_not_remove_an_existing_profile(namespace_worker, monkeypatch):
    calls = []

    def reject(command, **options):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(policy.subprocess, "run", reject)
    with pytest.raises(subprocess.CalledProcessError):
        with policy._authorized_launcher(env={}):
            pytest.fail("A rejected policy cannot launch qualification")
    assert len(calls) == 1 and "--add" in calls[0] and "--remove" not in calls[0]
    assert namespace_worker.read_bytes() == b"1\n"


def test_glob_in_temporary_parent_cannot_widen_profile_attachment(namespace_worker, tmp_path, monkeypatch):
    parent = tmp_path / "policy-*"
    parent.mkdir()
    monkeypatch.setattr(policy.tempfile, "tempdir", str(parent))
    calls = []
    monkeypatch.setattr(policy.subprocess, "run", lambda *args, **options: calls.append(args))
    with pytest.raises(RuntimeError, match="exact ASCII attachment"):
        with policy._authorized_launcher(env={}):
            pytest.fail("An AppArmor glob cannot authorize a CI launcher")
    assert calls == []
    assert list(parent.iterdir()) == []
