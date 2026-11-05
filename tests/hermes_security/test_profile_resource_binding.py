"""Logical named profiles retain one machine unlock without borrowing sibling stores."""
from pathlib import Path
import shutil

import pytest


@pytest.fixture
def machine(tmp_path, monkeypatch):
    from hermes_cli import profiles
    from hermes_security import io, vault
    from tools import mcp_tool_config

    root = tmp_path / "machine"
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: root)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: root / "profiles")
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    monkeypatch.setattr(mcp_tool_config, "_mcp_stderr_log_fh", {})
    vault.init_vault(root, "synthetic machine profile credential")
    vault.unlock(root, "synthetic machine profile credential")
    io.write_text(root / "config.yaml", "model: {provider: litellm, default: fixture}\n", purpose="config")
    io.write_text(root / ".env", "ACME_TOKEN=synthetic source account\n", purpose="env")
    yield root
    mcp_tool_config._close_mcp_stderr_logs()
    vault.clear_vault_cache()


def _profile(name, kind="fresh"):
    from hermes_cli import profiles

    return profiles.create_profile(name, no_alias=True, no_skills=kind == "fresh",
                                   clone_config=kind == "clone", clone_all=kind == "clone-all")


@pytest.mark.parametrize("kind", ["fresh", "clone", "clone-all"])
def test_public_profile_create_keeps_encrypted_log_and_artifact_consumers(machine, kind):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from hermes_security import artifacts, frames
    from tools import mcp_tool_config

    child = _profile("child", kind)
    assert not (child / ".hermes-vault").exists(), "parent metadata must never be copied"
    token = set_hermes_home_override(child)
    try:
        sink = mcp_tool_config._get_mcp_stderr_log()
        sink.write("private child diagnostic\n")
        assert b"private child diagnostic" in b"".join(frames.read_frames(sink.name, purpose="log"))
        target = child / "cache/spillover/result.txt"
        artifacts.write_bytes(target, b"private child result", home=child)
        assert artifacts.read_bytes(target, home=child) == b"private child result"
        assert b"private child result" not in target.read_bytes()
        assert b"private child diagnostic" not in Path(sink.name).read_bytes()
    finally:
        reset_hermes_home_override(token)


@pytest.mark.parametrize("purpose", ["log", "transcript"])
def test_shared_ancestor_frame_ciphertext_cannot_replay_in_another_profile(machine, purpose):
    from hermes_security import frames
    from hermes_security.errors import VaultError

    first, second = _profile("first"), _profile("second")
    a, b = first / "logs/private.log", second / "logs/private.log"
    frames.append(a, b"first profile only", purpose=purpose)
    frames.append(b, b"second profile only", purpose=purpose)
    original = a.read_bytes()
    b.write_bytes(original)
    with pytest.raises(VaultError):
        list(frames.read_frames(b, purpose=purpose))
    with pytest.raises(VaultError):
        frames.append(b, b"must not launder copied cipher", purpose=purpose)
    assert a.read_bytes() == original and b.read_bytes() == original


def test_shared_ancestor_envelope_aad_stays_bound_to_its_selected_profile(machine):
    from hermes_security import io
    from hermes_security.errors import VaultError

    first, second = _profile("first"), _profile("second")
    a, b = first / "private.json", second / "private.json"
    io.write_text(a, "first profile private envelope", purpose="state")
    original = a.read_bytes()
    b.write_bytes(original)
    with pytest.raises(VaultError):
        io.read_text(b, purpose="state")
    assert io.read_text(a, purpose="state") == "first profile private envelope"
    assert b.read_bytes() == original


@pytest.mark.parametrize("reunlock", [False, True])
def test_parent_lock_never_revives_retained_profile_sink(machine, reunlock):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from hermes_security import vault
    from hermes_security.errors import VaultError
    from tools import mcp_tool_config

    child = _profile("child")
    token = set_hermes_home_override(child)
    try:
        old = mcp_tool_config._get_mcp_stderr_log()
        old.write("before parent lock\n")
        before = Path(old.name).read_bytes()
        vault.lock_now(machine)
        if reunlock:
            vault.unlock(machine, "synthetic machine profile credential")
        with pytest.raises(VaultError):
            old.write("old callback after lock\n")
        assert Path(old.name).read_bytes() == before
        if reunlock:
            fresh = mcp_tool_config._get_mcp_stderr_log()
            assert fresh is not old and old.closed
            fresh.write("fresh exact parent authority\n")
    finally:
        reset_hermes_home_override(token)


def test_legacy_parent_frames_require_explicit_offline_rebind_and_keep_original(machine):
    from hermes_security import artifacts, frames, vault
    from hermes_security.errors import VaultError

    child = _profile("child")
    target = child / "logs/legacy.log"
    original = frames.encode_frames([b"complete legacy profile record"], vault=vault.get_vault(machine), purpose="log")
    target.write_bytes(original)
    target.chmod(0o600)
    with pytest.raises(VaultError):
        list(frames.read_frames(target, purpose="log"))
    assert target.read_bytes() == original
    from hermes_cli.profile_frame_repair import repair_profile_frames
    report = repair_profile_frames(child)
    assert report["retained"] == [] and report["rebound"] == ["logs/legacy.log"]
    assert b"".join(frames.read_frames(target, purpose="log")) == b"complete legacy profile record"
    from hermes_security import io
    assert any(io.read_bytes(p, purpose="state") == original for p in child.rglob("*.cipher"))
    assert artifacts._bound_custody(child).vault.home == machine


@pytest.mark.platforms("posix")
def test_retained_default_sink_refuses_replaced_logical_home_generation(machine):
    from tools import mcp_tool_config
    from hermes_security.errors import VaultError

    sink = mcp_tool_config._get_mcp_stderr_log()
    sink.write("original home generation\n")
    original = Path(sink.name).read_bytes()
    displaced = machine.with_name("retained-original")
    machine.rename(displaced)
    shutil.copytree(displaced, machine)
    with pytest.raises(VaultError):
        sink.write("must not follow replaced home\n")
    assert Path(sink.name).read_bytes() == original
    assert (displaced / "logs/mcp-stderr.log").read_bytes() == original


def test_config_only_profile_never_ignores_new_damaged_preferred_store(machine):
    from hermes_security import artifacts
    from hermes_security.errors import VaultError

    child = _profile("older")
    (child / ".env").unlink()
    custody = artifacts._bound_custody(child)
    target = child / "cache/spillover/result.txt"
    artifacts.write_bytes(target, b"original config-only profile", home=child, custody=custody)
    before = target.read_bytes()
    (child / ".env").write_bytes(b"HRMVAULT\0damaged preferred store")
    (child / ".env").chmod(0o600)
    with pytest.raises(VaultError):
        artifacts.write_bytes(target, b"must refuse", home=child, custody=custody)
    assert target.read_bytes() == before


def test_scoped_frames_clone_and_parent_owned_rename_rebind_only_the_new_copy(machine):
    from hermes_cli import profiles
    from hermes_security import frames, io

    first = _profile("first")
    log = first / "logs/scoped.log"
    frames.append(log, b"original scoped private record", purpose="log")
    original = log.read_bytes()
    second = profiles.create_profile("second", clone_from="first", clone_all=True, no_alias=True)
    assert list(frames.read_frames(second / "logs/scoped.log", purpose="log")) == [b"original scoped private record"]
    assert log.read_bytes() == original
    final = profiles.rename_profile("first", "renamed")
    assert not first.exists()
    assert list(frames.read_frames(final / "logs/scoped.log", purpose="log")) == [b"original scoped private record"]
    assert io.read_text(final / ".env", purpose="env")
    assert not (final / ".hermes-vault").exists()


@pytest.mark.parametrize("damage", ["truncated", "tampered"])
def test_offline_legacy_rebind_damaged_stream_is_retained_without_partial_backup(machine, damage):
    from hermes_cli.profile_frame_repair import repair_profile_frames
    from hermes_security import frames, vault

    child = _profile("legacy")
    raw = frames.encode_frames([b"legacy one", b"legacy two"], vault=vault.get_vault(machine), purpose="log")
    raw = raw[:-1] if damage == "truncated" else raw[:-1] + bytes([raw[-1] ^ 1])
    path = child / "logs/legacy.log"
    path.write_bytes(raw)
    path.chmod(0o600)
    report = repair_profile_frames(child)
    assert report["rebound"] == [] and len(report["retained"]) == 1
    assert path.read_bytes() == raw
    assert not list(child.rglob("*.cipher"))
