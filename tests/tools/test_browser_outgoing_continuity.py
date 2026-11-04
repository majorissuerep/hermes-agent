"""Outgoing browser continuity through real lifecycle APIs and real encrypted archives.

UNIT evidence: the browser is a wire stand-in and transports run fixed synthetic
filesystem helpers locally. RAM/capacity reports are explicitly labelled seams;
this does not qualify a real SSH/Docker/Chromium product route.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import pytest

from hermes_security import browser_profile_archive as archive, vault
from tests.hermes_cli.browser_profile_env import cookie_db


@pytest.fixture
def outgoing(tmp_path, monkeypatch):
    from hermes_platform import client_capture, memory_capacity, remote_workspace
    from hermes_platform.memory_capacity import CapacityBudget
    from hermes_platform.memory_protection import RamProof
    from hermes_security import protected_workspace
    from tools import browser_terminal as terminal, browser_tool, browser_tool_session as session
    from tools.environments import streams

    home, ram = tmp_path / "home", tmp_path / "unit-ram"
    home.mkdir(mode=0o700)
    ram.mkdir(mode=0o700)
    remote_home = tmp_path / "remote-account"
    remote_home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(home, "synthetic outgoing continuity password")
    vault.unlock(home, "synthetic outgoing continuity password")
    monkeypatch.setattr(remote_workspace, "_prove", lambda *_args: RamProof(True, "ramfs", "unit target RAM seam"))
    monkeypatch.setattr(memory_capacity, "capacity_budget", lambda *_args, **_kw: CapacityBudget(8 * 1024**3, "unit capacity seam"))

    @contextmanager
    def workspace(_purpose, **_kwargs):
        with tempfile.TemporaryDirectory(dir=ram) as directory:
            yield Path(directory)
    monkeypatch.setattr(protected_workspace, "protected_workspace", workspace)
    monkeypatch.setattr(protected_workspace, "protected_workspace_base", lambda: ram)
    monkeypatch.setattr(protected_workspace, "protected_workspace_status", lambda *_a, **_k: RamProof(True, "ramfs", "unit host RAM seam"))
    selected, wire = {}, {}

    def choose(name):
        base = tmp_path / name
        base.mkdir(mode=0o700)
        base.chmod(0o700)
        env = SimpleNamespace(host="synthetic.invalid", user="unit", port=22)
        env.get_temp_dir = lambda: str(base)
        selected["env"] = env
        return env

    def run_in(_env, argv, **kwargs):
        return subprocess.run(argv, capture_output=True, input=kwargs.get("stdin"),
                              timeout=kwargs.get("timeout", 30), check=False)

    def command(_env, argv, **kwargs):
        if kwargs.get("child_env", {}).get("AGENT_BROWSER_PROFILE"):
            wire.update(kwargs["child_env"])
        return list(argv)

    original_client = client_capture.run_client
    def client(argv, **kwargs):
        payload = kwargs.get("payload")
        if payload and payload.startswith(b'[["memory_protection"'):
            facts = {"available_bytes": 8 * 1024**3, "reason": "unit target capacity seam",
                     "uid": os.getuid(), "home": str(remote_home), "machine": "a" * 64}
            return 0, json.dumps(facts).encode(), b""
        return original_client(argv, **kwargs)

    def browser(_argv, _env, _payload, _timeout):
        return 0, json.dumps({"success": wire.get("stop_ok", True), "data": {}}), ""

    monkeypatch.setattr(client_capture, "run_client", client)
    monkeypatch.setattr(streams, "run_in", run_in)
    monkeypatch.setattr(streams, "remote_command", command)
    monkeypatch.setattr(streams, "exec_prefix", lambda *_args, **_kw: ["unit-target"])
    monkeypatch.setattr(terminal, "terminal_backend", lambda: "ssh")
    monkeypatch.setattr(terminal, "terminal_environment", lambda **_kwargs: selected["env"])
    monkeypatch.setattr(session, "_browser_command_preflight", lambda: {"browser_cmd": "agent-browser"})
    monkeypatch.setattr(session, "_run_agent_browser_client", browser)
    monkeypatch.setattr(session._cloud, "_get_browser_engine", lambda: "chrome")
    monkeypatch.setattr(session._cloud, "_is_headed_mode", lambda: False)
    monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
    monkeypatch.setattr(browser_tool, "_socket_safe_tmpdir", lambda: str(ram))
    monkeypatch.setattr(session, "_get_session_info", lambda task: browser_tool._active_sessions[task])
    browser_tool._active_sessions.clear()
    browser_tool._session_owner_homes.clear()
    def start(task):
        browser_tool._active_sessions[task] = {"session_name": "h_" + task, "bb_session_id": None,
                                               "cdp_url": None, "features": {"local": True}}
        browser_tool._session_owner_homes[task] = str(home)
        result = session._run_browser_command(task, "open", ["about:blank"])
        assert result["success"] is True
        return Path(wire["AGENT_BROWSER_PROFILE"])
    choose("target-one")
    yield SimpleNamespace(home=home, ram=ram, wire=wire, choose=choose, start=start, session=session)
    module = sys.modules.get("tools.browser_outgoing_profile")
    if module is not None:
        module._LEASES.clear()
    browser_tool._active_sessions.clear()
    browser_tool._session_owner_homes.clear()
    vault.clear_vault_cache()


def test_confirmed_close_checkpoints_and_fresh_environment_restores_private_state(outgoing):
    from tools.browser_tool_lifecycle import cleanup_browser

    profile = outgoing.start("first")
    private = secrets.token_hex(24)
    cookie_db(profile / "Default/Network/Cookies", private)
    site = profile / "Default/Service Worker/CacheStorage/store"
    site.parent.mkdir(parents=True)
    site.write_bytes(private.encode())
    cleanup_browser("first")
    saved = archive.archive_paths(outgoing.home)
    assert len(saved) == 1, "normal close lost durable continuity: no encrypted host checkpoint"
    ciphertext = saved[0].read_bytes()
    assert private.encode() not in ciphertext
    assert list(profile.iterdir()) == []

    outgoing.choose("target-two")
    restored = outgoing.start("second")
    assert restored != profile
    assert cookie_db(restored / "Default/Network/Cookies") == private
    assert (restored / "Default/Service Worker/CacheStorage/store").read_bytes() == private.encode()
    assert saved[0].read_bytes() == ciphertext
    assert not any(private.encode() in path.read_bytes() for path in outgoing.home.rglob("*")
                   if path.is_file() and not path.is_symlink())
    cleanup_browser("second")


def test_failed_close_retains_tracking_and_private_ram(outgoing):
    from tools import browser_tool
    from tools.browser_tool_lifecycle import cleanup_browser

    profile = outgoing.start("pending")
    private = secrets.token_bytes(32)
    target = profile / "Cookies"
    target.write_bytes(private)
    outgoing.wire["stop_ok"] = False
    with pytest.raises(RuntimeError):
        cleanup_browser("pending")
    assert "pending" in browser_tool._active_sessions
    assert target.read_bytes() == private
    assert archive.archive_paths(outgoing.home) == []


def test_explicit_orphan_repair_requires_ended_owner_and_exact_ram_binding(outgoing, monkeypatch):
    from tools import browser_outgoing_profile as profiles, browser_outgoing_recovery as recovery
    from tools import browser_recording_pending as recording
    from tools.browser_tool_lifecycle import cleanup_browser

    profile = outgoing.start("orphan")
    cookie_db(profile / "Cookies", "synthetic-recovery-only")
    lease = profiles.lookup("orphan")
    old_ref = (outgoing.home / "state/browser-outgoing" / (lease.digest + ".json")).read_bytes()
    outgoing.wire["stop_ok"] = False
    with pytest.raises(RuntimeError):
        cleanup_browser("orphan")
    profiles._LEASES.clear()  # model process restart; repair uses its own new custody
    refused = recovery.recover(outgoing.home)
    assert refused["saved"] == [] and "live or unknown" in refused["retained"][0]
    assert (outgoing.home / "state/browser-outgoing" / (lease.digest + ".json")).read_bytes() == old_ref
    monkeypatch.setattr(recording, "_owner_state", lambda _owner: "dead")
    binding = profile / ".hermes-browser-binding.json"
    original = binding.read_bytes()
    binding.write_bytes(original.replace(lease.token.encode(), b"b" * 32))
    refused = recovery.recover(outgoing.home)
    assert refused["saved"] == [] and refused["retained"]
    assert cookie_db(profile / "Cookies") == "synthetic-recovery-only"
    assert archive.archive_paths(outgoing.home) == []
    binding.write_bytes(original)
    repaired = recovery.recover(outgoing.home)
    assert len(repaired["saved"]) == 1 and repaired["retained"] == []
    assert list(profile.iterdir()) == []
    outgoing.wire["stop_ok"] = True
    outgoing.choose("after-repair")
    restored = outgoing.start("after-repair")
    assert cookie_db(restored / "Cookies") == "synthetic-recovery-only"
    cleanup_browser("after-repair")


def test_outgoing_operation_dies_with_exact_source_but_sibling_remains_usable(outgoing):
    from agent.session_context import scoped_current_session_id
    from hermes_security.session_authority import start_session_authority
    from hermes_security.errors import VaultLockedError
    from tools import browser_outgoing_profile as profiles
    from tools.browser_tool_lifecycle import cleanup_browser

    first = start_session_authority(outgoing.home, "A")
    sibling = start_session_authority(outgoing.home, "B")
    try:
        with scoped_current_session_id("A"):
            profile = outgoing.start("source-a")
            cookie_db(profile / "Cookies", "only-source-A")
        reference = next((outgoing.home / "state/browser-outgoing").glob("*.json")).read_bytes()
        first.close()
        with scoped_current_session_id("B"):
            with pytest.raises(VaultLockedError):
                cleanup_browser("source-a")
            assert cookie_db(profile / "Cookies") == "only-source-A"
            assert next((outgoing.home / "state/browser-outgoing").glob("*.json")).read_bytes() == reference
            profiles.lookup("source-a").custody.resource.check()
            from hermes_security.session_custody import SessionCustody
            sibling_operation = SessionCustody.capture(outgoing.home)
            assert len(sibling_operation.derive_key("sibling-still-live")) == 32
        assert vault.is_unlocked(outgoing.home)
    finally:
        first.close()
        sibling.close()


def test_explicit_legacy_conversion_preserves_old_source_until_verified_and_usable(outgoing):
    from tools import browser_terminal, browser_outgoing_profile as profiles
    from tools import browser_outgoing_recovery as recovery
    from tools.browser_tool_lifecycle import cleanup_browser

    env = browser_terminal.terminal_environment()
    runtime = browser_terminal.browser_runtime(env)
    lease = profiles._new_lease("legacy-inventory", {}, env, runtime, "")
    legacy = Path(profiles._legacy_path(lease))
    legacy.mkdir(mode=0o700, parents=True)
    cookie_db(legacy / "Cookies", "legacy-only-private-cookie")
    before = (legacy / "Cookies").read_bytes()
    with pytest.raises(RuntimeError, match="older browser profile"):
        profiles._legacy_absent(lease)
    assert (legacy / "Cookies").read_bytes() == before
    report = recovery.migrate_legacy(outgoing.home)
    assert report["retained"] == [] and len(report["saved"]) == 1
    assert not legacy.exists()
    profile = outgoing.start("after-legacy")
    assert cookie_db(profile / "Cookies") == "legacy-only-private-cookie"
    cleanup_browser("after-legacy")


def test_ordinary_teardown_retains_only_copy_tracking_and_restore_refuses_pending_refs(outgoing, monkeypatch):
    from hermes_cli.backup_browser_profiles import restore_guard
    from hermes_state.runtime_ownership import OwnershipConflict
    from tools import terminal_tool, terminal_tool_lifecycle as lifecycle, browser_outgoing_profile as profiles

    profile = outgoing.start("retained")
    cookie_db(profile / "Cookies", "retained-only-copy")
    lease = profiles.lookup("retained")
    outgoing.wire["stop_ok"] = False
    monkeypatch.setitem(terminal_tool._active_environments, "retained", lease.env)
    monkeypatch.setitem(terminal_tool._last_activity, "retained", 0)
    with pytest.raises(RuntimeError, match="close failed"):
        lifecycle.cleanup_vm("retained")
    assert terminal_tool._active_environments["retained"] is lease.env
    assert cookie_db(profile / "Cookies") == "retained-only-copy"
    assert archive.archive_paths(outgoing.home) == []
    ref = Path("state/browser-outgoing") / (lease.digest + ".json")
    with pytest.raises(OwnershipConflict, match="Outgoing browser RAM/recovery"):
        with restore_guard(outgoing.home, [ref]):
            raise AssertionError("pending reference would have been replaced without archive members")


def test_revoked_current_source_cannot_use_another_live_source_browser(outgoing):
    from hermes_security.session_authority import start_session_authority
    from agent.session_context import scoped_current_session_id
    from hermes_security.errors import VaultLockedError
    first=start_session_authority(outgoing.home,'A')
    other=start_session_authority(outgoing.home,'B')
    try:
        with scoped_current_session_id('A'):
            outgoing.start('original-A')
        other.close()
        with scoped_current_session_id('B'):
            with pytest.raises(VaultLockedError):
                outgoing.session._run_browser_command('original-A','get',['title'])
    finally:
        first.close();other.close()


def test_fresh_source_reuses_completed_profile_resource_without_reviving_old_task(outgoing):
    from agent.session_context import scoped_current_session_id
    from hermes_security.session_authority import start_session_authority
    from hermes_security.errors import VaultLockedError
    from tools import browser_outgoing_profile as profiles, browser_outgoing_transfer as transfer
    from tools.browser_tool_lifecycle import cleanup_browser

    first=start_session_authority(outgoing.home,'completed-A')
    sibling=start_session_authority(outgoing.home,'fresh-B')
    try:
        with scoped_current_session_id('completed-A'):
            profile=outgoing.start('completed-A')
            cookie_db(profile/'Cookies','shared-profile-cookie')
            old=profiles.lookup('completed-A')
            cleanup_browser('completed-A')
        first.close()
        with scoped_current_session_id('fresh-B'):
            outgoing.choose('fresh-source-target')
            reopened=outgoing.start('fresh-B')
            fresh=profiles.lookup('fresh-B')
            assert fresh.custody.resource.vault is old.custody.resource.vault
            assert cookie_db(reopened/'Cookies')=='shared-profile-cookie'
            with pytest.raises(VaultLockedError):
                transfer.capacity(old)
            fresh.custody.check()
            cleanup_browser('fresh-B')
    finally:
        first.close();sibling.close()
