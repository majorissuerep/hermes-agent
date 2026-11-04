"""Pending browser recordings survive their process and are sealed only by explicit, proven repair.

UNIT evidence only: the RAM-only mount proof is replaced by a stand-in seam and the recording
process identity is a real local child pinned through that seam. Native RAM proof and the real
recorder/browser lifecycle are qualified separately on their hosts.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid

import pytest

from hermes_cli.browser_recording_repair import recover_pending
from tools import browser_recording_pending as pending

PASSWORD = "synthetic pending recording test password"
VIDEO = b"\x1a\x45\xdf\xa3" + b"synthetic-webm-frames" * 64


@pytest.fixture
def homes(tmp_path, monkeypatch):
    from hermes_platform.memory_protection import RamProof
    from hermes_security import protected_workspace, vault

    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    created = []
    for label in ("owner", "other"):
        home = (tmp_path / label).resolve()
        home.mkdir()
        vault.init_vault(home, PASSWORD)
        vault.unlock(home, PASSWORD)
        created.append(home)
    monkeypatch.setenv("HERMES_HOME", str(created[0]))
    ram = (tmp_path / "ram").resolve()
    ram.mkdir(mode=0o700)
    monkeypatch.setattr(protected_workspace, "protected_workspace_base", lambda: ram)
    monkeypatch.setattr(pending, "_prove", lambda path: RamProof(True, "tmpfs", "unit RAM seam"))
    yield created[0], created[1], ram
    vault.clear_vault_cache()


def _name() -> str:
    return f"browser_recording_20261104T120000Z_{uuid.uuid4().hex}.webm"


def _spawn() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)


def _identity(child: subprocess.Popen) -> dict:
    from hermes_platform.process_identity import pid_namespace_id
    from hermes_platform.processes import get_process_start_time

    return {"pid": child.pid, "start_time": get_process_start_time(child.pid), "pidns": pid_namespace_id(),
            "uid": os.getuid()}


def _end(child: subprocess.Popen) -> None:
    child.stdin.close()
    child.wait(timeout=30)


def _videos(home):
    folder = home.joinpath(*pending.VIDEO_SUBDIR)
    return sorted(folder.iterdir()) if folder.exists() else []


@pytest.mark.platforms("posix")
def test_unsaved_recording_outlives_its_process_and_only_proven_repair_seals_it(homes, monkeypatch):
    from hermes_security import artifacts, vault
    from hermes_security.errors import VaultError

    home, _, ram = homes
    recorder = _spawn()
    try:
        identity = _identity(recorder)
        monkeypatch.setattr(pending, "_owner_identity", lambda: identity)
        custody = artifacts._bound_custody(home)
        name = _name()
        lease = pending.allocate(home, custody, name)
        assert lease.workspace.parent == ram
        lease.stage.write_bytes(VIDEO)
        pending.completed(lease, custody)

        vault.lock_now(home)
        vault.unlock(home, PASSWORD)  # a later unlock is never adopted by the original custody
        with pytest.raises(VaultError):
            pending.publish(lease, custody)
        assert lease.stage.read_bytes() == VIDEO and lease.locator.exists()

        while_running = recover_pending(home)
        assert while_running["saved"] == [] and len(while_running["retained"]) == 1
        assert lease.stage.read_bytes() == VIDEO and _videos(home) == []
    finally:
        _end(recorder)

    report = recover_pending(home)

    destination = home.joinpath(*pending.VIDEO_SUBDIR) / name
    assert report == {"saved": [destination.relative_to(home).as_posix()], "retained": []}
    assert artifacts.read_bytes(destination, home=home) == VIDEO
    assert VIDEO not in destination.read_bytes()
    assert not lease.workspace.exists() and not lease.locator.exists()
    assert recover_pending(home) == {"saved": [], "retained": []}


@pytest.mark.platforms("posix")
def test_repair_never_seals_or_deletes_unstopped_replaced_or_foreign_captures(homes, monkeypatch):
    from hermes_security import artifacts

    home, other, _ = homes
    recorder = _spawn()
    identity = _identity(recorder)
    _end(recorder)
    monkeypatch.setattr(pending, "_owner_identity", lambda: identity)
    custody = artifacts._bound_custody(home)

    unstopped = pending.allocate(home, custody, _name())
    unstopped.stage.write_bytes(VIDEO)  # record stop was never acknowledged
    replaced = pending.allocate(home, custody, _name())
    replaced.stage.write_bytes(VIDEO)
    pending.completed(replaced, custody)
    replaced.stage.unlink()
    replaced.stage.write_bytes(b"planted after completion")
    foreign_custody = artifacts._bound_custody(other)
    foreign = pending.allocate(other, foreign_custody, _name())
    foreign.stage.write_bytes(VIDEO)
    pending.completed(foreign, foreign_custody)
    planted = home.joinpath(*pending.LOCATOR_SUBDIR) / foreign.locator.name
    planted.write_bytes(foreign.locator.read_bytes())  # profile B's locator copied into profile A
    os.chmod(planted, 0o600)

    report = recover_pending(home)

    assert report["saved"] == [] and len(report["retained"]) == 3
    assert _videos(home) == [] and _videos(other) == []
    assert unstopped.stage.read_bytes() == VIDEO and unstopped.locator.exists()
    assert replaced.stage.read_bytes() == b"planted after completion" and replaced.locator.exists()
    assert foreign.stage.read_bytes() == VIDEO and foreign.locator.exists() and planted.exists()


@pytest.mark.platforms("posix")
def test_verified_recording_and_failed_retirement_remain_distinct_and_retryable(homes, monkeypatch):
    from hermes_security import artifacts

    home, _, _ = homes
    recorder = _spawn()
    identity = _identity(recorder)
    _end(recorder)
    monkeypatch.setattr(pending, "_owner_identity", lambda: identity)
    custody = artifacts._bound_custody(home)
    lease = pending.allocate(home, custody, _name())
    pending.store_capture(lease, custody, VIDEO)
    pending.completed(lease, custody)
    unlink, failures = pending._unlink_locator, []

    def fail_once(selected):
        if not failures:
            failures.append(True)
            raise OSError("synthetic directory synchronization failure")
        return unlink(selected)

    monkeypatch.setattr(pending, "_unlink_locator", fail_once)
    first = recover_pending(home)
    [video] = _videos(home)
    encoded = video.read_bytes()
    assert first["saved"] == [video.relative_to(home).as_posix()]
    assert len(first["retained"]) == 1 and "cleanup incomplete" in first["retained"][0]
    assert artifacts.read_bytes(video, home=home) == VIDEO
    assert lease.locator.exists() and not lease.stage.exists()
    assert recover_pending(home) == {"saved": [video.relative_to(home).as_posix()], "retained": []}
    assert video.read_bytes() == encoded and not lease.workspace.exists()
