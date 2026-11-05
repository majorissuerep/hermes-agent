"""A backend directory counts as protected only when the backend itself proves never-swapped RAM.

The control host's protected_work_dir cannot attest a container or SSH filesystem. Unit cases fake
only the exec transport (``tests/tools/_protected_backend.py``); the Linux cases run the real probe
script through ``sh`` against this kernel and compare its verdict with the host's own proof.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.tools._protected_backend import ACTIVE_SWAP, FakeBackend

pytest_plugins = ["tests.hermes_cli.test_managed_agents_public_cli"]


@pytest.fixture
def transport(monkeypatch):
    def install(backend):
        from tools.environments import streams
        monkeypatch.setattr(streams, "run_in", backend.run_in)
        return backend
    return install


@pytest.mark.parametrize("backend", [
    FakeBackend([("/tmp", "tmpfs", "rw,size=512m")], swaps=ACTIVE_SWAP),
    FakeBackend([("/tmp", "tmpfs", "rw")], swaps="garbage"),
    FakeBackend([("/tmp", "ext4", "rw")]),
    FakeBackend([("/scratch", "ramfs", "rw")], temp="/scratch", modes={"/scratch": "777"}),
], ids=["tmpfs-active-swap", "tmpfs-unknown-swap", "disk", "world-writable-without-sticky"])
def test_unproven_backend_directory_is_refused_before_anything_is_created(transport, backend):
    from hermes_platform import remote_workspace as protected_scratch

    transport(backend)
    with pytest.raises(protected_scratch.ProtectedTargetUnavailable) as refused:
        protected_scratch.acquire(lambda argv, **kw: backend.run_in(backend, argv, **kw), "hermes-browser-0123", ("profile",), candidates=(backend.temp,))
    assert backend.temp in str(refused.value)
    assert backend.probes("provision") == []
    assert backend.dirs == {}


def test_truncated_mount_table_is_unknown_not_a_proof(transport, monkeypatch):
    """A later over-mount could hide past the probe bound, so a cut table never proves RAM."""
    from hermes_platform import remote_workspace as protected_scratch

    backend = transport(FakeBackend([("/tmp", "tmpfs", "rw,noswap")]))
    monkeypatch.setattr(protected_scratch, "MOUNTINFO_LIMIT", len(backend.mountinfo()) - 1)
    with pytest.raises(protected_scratch.ProtectedTargetUnavailable, match="probe bound"):
        protected_scratch.acquire(lambda argv, **kw: backend.run_in(backend, argv, **kw), "hermes-browser-0123", ("profile",), candidates=("/tmp",))
    assert backend.probes("provision") == []


def test_proven_directory_is_owner_only_reused_and_foreign_or_linked_state_is_refused(transport):
    from hermes_platform import remote_workspace as protected_scratch

    backend = transport(FakeBackend([("/run/user/1000", "tmpfs", "rw,noswap")], xdg="/run/user/1000",
                                    swaps=ACTIVE_SWAP))
    found = protected_scratch.acquire(lambda argv, **kw: backend.run_in(backend, argv, **kw), "hermes-browser-0123", ("profile", "tmp"), candidates=("/tmp",))
    assert found.root == "/run/user/1000/hermes-browser-0123"
    assert found.children == {"profile": f"{found.root}/profile", "tmp": f"{found.root}/tmp"}
    assert found.proof.proven and found.proof.reason == "tmpfs is mounted with noswap"
    again = protected_scratch.acquire(lambda argv, **kw: backend.run_in(backend, argv, **kw), "hermes-browser-0123", ("profile", "tmp"), candidates=("/tmp",))
    assert again == found  # profile continuity: the same proven directory is reused

    backend.dirs[f"{found.root}/profile"] = "0:700"  # replaced by another account
    with pytest.raises(protected_scratch.ProtectedTargetUnavailable, match="refused:profile"):
        protected_scratch.acquire(lambda argv, **kw: backend.run_in(backend, argv, **kw), "hermes-browser-0123", ("profile", "tmp"), candidates=("/tmp",))
    assert backend.dirs[f"{found.root}/profile"] == "0:700"  # retained for inspection, not deleted


def _local_transport(monkeypatch):
    """Run the production probe argv on this host's real ``sh`` and kernel."""
    from tools.environments import streams

    def run_in(_env, argv, *, child_env=None, timeout=30.0, **_kwargs):
        assert not child_env
        return subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
    return lambda argv, **kw: run_in(None, argv, **kw)


@pytest.mark.platforms("linux")
def test_real_probe_reaches_the_same_verdict_as_the_host_proof(monkeypatch, tmp_path):
    from hermes_platform.memory_protection import ram_only_directory_proof
    from hermes_platform import remote_workspace as protected_scratch

    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    run = _local_transport(monkeypatch)
    # The autouse synthetic home also lives under tmp_path. Only this dedicated
    # target base is under the probe's ownership/nonmutation contract.
    base = tmp_path / "backend-scratch"
    base.mkdir(mode=0o700)
    expected = ram_only_directory_proof(base)
    if not expected.proven:
        with pytest.raises(protected_scratch.ProtectedTargetUnavailable, match=expected.reason):
            protected_scratch.acquire(run, "hermes-probe-test", ("profile",), candidates=(str(base),))
        assert list(base.iterdir()) == []
        return
    found = protected_scratch.acquire(run, "hermes-probe-test", ("profile",), candidates=(str(base),))
    root = Path(found.root)
    assert root.parent == Path(os.path.realpath(base))
    for path in (root, root / "profile"):
        assert path.is_dir() and not path.is_symlink()
        assert (path.stat().st_mode & 0o7777, path.stat().st_uid) == (0o700, os.geteuid())


@pytest.mark.platforms("linux")
def test_real_probe_never_provisions_through_a_planted_link(monkeypatch, ram_runtime):
    from hermes_platform.memory_protection import ram_only_directory_proof
    from hermes_platform import remote_workspace as protected_scratch

    base = ram_runtime
    assert ram_only_directory_proof(base).proven
    run = _local_transport(monkeypatch)
    decoy = Path(base) / f"hermes-decoy-{os.getpid()}"
    link = Path(base) / f"hermes-linked-{os.getpid()}"
    decoy.mkdir(mode=0o700)
    link.symlink_to(decoy)
    try:
        with pytest.raises(protected_scratch.ProtectedTargetUnavailable, match="refused"):
            protected_scratch.acquire(run, link.name, ("profile",))
        assert link.is_symlink() and list(decoy.iterdir()) == []
    finally:
        link.unlink()
        decoy.rmdir()
