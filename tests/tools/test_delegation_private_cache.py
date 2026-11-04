"""Delegation's common producers keep private state encrypted and recoverable."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_security import artifacts, frames, vault
from hermes_security.delegation_state import DelegationStore, read_manifest
from hermes_security.errors import VaultError, VaultNotInitializedError
from hermes_security.migrate import repair_clobbered_state, scan_for_plaintext
from tools import delegation_live_log as live
from tools.delegate_tool_results import _trim_summary_with_footer
from tools.private_artifact_files import read_file

PASSWORD = "synthetic delegation custody password"


@pytest.fixture
def home(tmp_path, monkeypatch):
    selected = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(selected))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(selected, PASSWORD)
    vault.unlock(selected, PASSWORD)
    yield selected
    vault.clear_vault_cache()


@pytest.mark.parametrize("layout", ["current", "legacy", "alias"])
def test_common_producers_preserve_ciphertext_and_all_authenticated_views(home, tmp_path, monkeypatch, layout):
    selected = home
    if layout == "legacy":
        (home / "delegation_cache" / "live").mkdir(parents=True)
    if layout == "alias":
        selected = tmp_path / "profile-alias"
        selected.symlink_to(home, target_is_directory=True)
        monkeypatch.setenv("HERMES_HOME", str(selected))
    goal = "synthetic private child goal 652f9e"
    delegation_id, writers, paths = live.create_live_transcripts([{"goal": goal}], home=selected)
    assert delegation_id and len(paths) == 1
    writer = writers[0]
    writer.assistant_text("private result for " + goal)
    log = Path(paths[0])
    if layout == "legacy":
        assert "delegation_cache" in log.parts
    manifest = log.parent / "manifest.json"
    live.update_manifest_statuses(delegation_id, [{"task_index": 0, "status": "completed"}],
                                  home=selected, store=writer.store)
    assert writer.store.read_manifest(manifest)["tasks"][0]["status"] == "completed"
    summary = "summary-start\n" + goal * 3000 + "\nsummary-end"
    rendered, summary_path = _trim_summary_with_footer(summary, 2000, 0, store=writer.store)
    assert summary_path and "read_file" in rendered
    assert artifacts.read_text(summary_path, home=selected) == summary
    assert goal in json.loads(read_file(str(log), 1, 200, 100_000))["content"]
    assert goal in json.loads(read_file(summary_path, 1, 200, 100_000))["content"]
    with pytest.raises(PermissionError):
        read_file(str(manifest), 1, 100, 100_000)
    for path in (log, manifest, Path(summary_path)):
        assert path.read_bytes().startswith(b"HRMVAULT\x00")
        assert goal.encode() not in path.read_bytes()
    assert scan_for_plaintext(home) == []
    manifest_before = manifest.read_bytes()
    manifest.write_bytes(manifest_before[:-1] + bytes([manifest_before[-1] ^ 1]))
    with pytest.raises(VaultError):
        writer.store.read_manifest(manifest)
    assert manifest.relative_to(selected).as_posix() in scan_for_plaintext(home)


def test_original_writer_and_status_store_stay_revoked_and_missing_profile_never_borrows(home):
    from hermes_cli.cli_subagent_monitor import read_tail

    delegation_id, writers, paths = live.create_live_transcripts([{"goal": "preserved original task"}], home=home)
    writer = writers[0]
    path = Path(paths[0])
    manifest = path.parent / "manifest.json"
    before = path.read_bytes(), manifest.read_bytes()
    missing = home / "profiles" / "missing"
    with pytest.raises(VaultNotInitializedError):
        DelegationStore(missing)
    assert vault.is_unlocked(home)
    # An old batch with no captured resource cannot borrow this current unlock.
    rendered, spill = _trim_summary_with_footer("private unbound summary" * 3000, 2000, 0, store=None)
    assert spill is None and "could not be stored" in rendered
    assert not list(home.rglob("subagent-summary*.txt"))
    vault.lock_now(home)
    vault.unlock(home, PASSWORD)
    writer.event("assistant", "must not revive old custody")
    live.update_manifest_statuses(delegation_id, [{"task_index": 0, "status": "completed"}],
                                  home=home, store=writer.store)
    assert (path.read_bytes(), manifest.read_bytes()) == before
    assert writer.error and not writer._ok
    with pytest.raises(VaultError):
        writer.store.read_log(path)
    assert "preserved original task" not in read_tail(path, store=writer.store)
    fresh = DelegationStore(home)
    assert b"preserved original task" in fresh.read_log(path)
    assert fresh.read_manifest(manifest)["tasks"][0]["status"] == "running"
    fresh.append_log(path, "new authorized owner\n")
    assert "new authorized owner" in read_tail(path, store=fresh)


@pytest.mark.parametrize("legacy_frames", [False, True])
def test_explicit_repair_converts_legacy_delegation_without_runtime_fallback(home, legacy_frames):
    directory = home / "delegation_cache" / "live" / "deleg_old"
    directory.mkdir(parents=True)
    log, manifest = directory / "task-0.log", directory / "manifest.json"
    message = b"synthetic old private child goal\n"
    payload = {"delegation_id": "deleg_old", "tasks": [{"index": 0, "goal": message.decode()}]}
    if legacy_frames:
        frames.append(log, message, purpose="log")
        vault.get_vault(home).write_bytes(manifest, json.dumps(payload).encode(), purpose="state")
    else:
        log.write_bytes(message)
        manifest.write_text(json.dumps(payload), encoding="utf-8")
    for parent in (directory, *directory.parents):
        if parent.is_relative_to(home):
            parent.chmod(0o700)
    log.chmod(0o600)
    manifest.chmod(0o600)
    before = log.read_bytes(), manifest.read_bytes()
    with pytest.raises(VaultError):
        artifacts.read_bytes(log, home=home)
    with pytest.raises((VaultError, PermissionError)):
        read_manifest(manifest, home=home)
    assert (log.read_bytes(), manifest.read_bytes()) == before
    assert {log.relative_to(home).as_posix(), manifest.relative_to(home).as_posix()} <= set(scan_for_plaintext(home))
    report = repair_clobbered_state(home)
    assert not report["unrecoverable"]
    assert artifacts.read_bytes(log, home=home) == message
    assert read_manifest(manifest, home=home) == payload
    assert scan_for_plaintext(home) == []
    with pytest.raises(PermissionError):
        artifacts.read_bytes(manifest, home=home)
