"""browser.record_sessions contracts: RAM-only capture, encrypted retention, original custody.

UNIT evidence only: the fake agent-browser, fake outgoing transport and the RAM workspace stand-in
replace the native RAM proof and real backends. Native/macOS/remote proof runs on those hosts.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tools import browser_recording as rec_mod
from tools.browser_terminal import BrowserRuntime

PASSWORD = "synthetic recording test password"
VIDEO = b"\x1a\x45\xdf\xa3" + b"synthetic-webm-frames" * 64


@pytest.fixture
def owner(tmp_path, monkeypatch):
    from hermes_security import vault
    from hermes_security import protected_workspace
    from hermes_platform.memory_protection import RamProof
    from tools import browser_recording_pending as pending

    home = (tmp_path / "owner").resolve()
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(home, PASSWORD)
    vault.unlock(home, PASSWORD)
    ram = tmp_path / "ram"
    ram.mkdir(mode=0o700)

    monkeypatch.setattr(protected_workspace, "protected_workspace_base", lambda: ram)
    monkeypatch.setattr(pending, "_prove", lambda path: RamProof(True, "tmpfs", "unit RAM seam"))
    monkeypatch.setattr(rec_mod, "_capture_driver", lambda key, home: ({"session_name": key}, "fake-browser", "chromium"))
    monkeypatch.setattr(rec_mod, "_enabled", lambda: True)
    monkeypatch.setattr("tools.terminal_tool._get_env_config", lambda: {"env_type": "local"})
    rec_mod._RECORDINGS.clear()
    yield home, ram
    rec_mod._RECORDINGS.clear()
    vault.clear_vault_cache()


class FakeBrowser:
    def __init__(self, foreign: Path | None = None, stop_ok=True, writes_local=True):
        self.calls, self.capture, self.foreign = [], None, foreign
        self.stop_ok, self.writes_local = stop_ok, writes_local
        self.contexts = []

    def __call__(self, key, command, args=None, **_kw):
        from hermes_constants import get_hermes_home
        from agent.secret_scope import get_secret

        self.contexts.append((get_hermes_home().resolve(), get_secret("RECORDING_CONTEXT_TOKEN")))
        self.calls.append((key, command, list(args or [])))
        if command == "record" and args[0] == "start":
            self.capture = args[1]
            return {"success": True}
        if command == "record" and args[0] == "stop":
            if not self.stop_ok:
                return {"success": False, "error": "daemon gone"}
            if self.writes_local:
                Path(self.capture).write_bytes(VIDEO)
            return {"success": True, "data": {"path": str(self.foreign or "")}}
        return {"success": True}

    def stops(self):
        return [c for c in self.calls if c[1] == "record" and c[2][0] == "stop"]


def _bind_browser(monkeypatch, browser):
    monkeypatch.setattr(rec_mod, "_command", lambda selected, args: browser(selected.session_key, "record", args))


def _videos(home):
    folder = home / "artifacts" / "media" / "videos"
    return sorted(folder.iterdir()) if folder.exists() else []


def test_native_recording_is_sealed_once_and_leaves_no_plaintext(owner, tmp_path, monkeypatch):
    from hermes_security import artifacts

    home, ram = owner
    foreign = tmp_path / "foreign.webm"
    foreign.write_bytes(b"not ours")
    browser = FakeBrowser(foreign=foreign)
    _bind_browser(monkeypatch, browser)

    rec_mod.start("task-a")
    assert Path(browser.capture).is_relative_to(ram)
    sealed = rec_mod.stop("task-a")

    assert _videos(home) == [sealed]
    assert rec_mod._NAME.fullmatch(sealed.name)
    assert VIDEO not in sealed.read_bytes()
    assert artifacts.read_bytes(sealed, home=home) == VIDEO
    assert list(ram.iterdir()) == []  # RAM capture retired only after publication
    assert foreign.read_bytes() == b"not ours"  # daemon-reported path ignored
    assert not (home / "browser_recordings").exists()
    assert len(browser.stops()) == 1


def test_lock_then_fresh_unlock_never_publishes_stale_recording(owner, monkeypatch):
    from hermes_security import vault
    from tools import browser_tool_lifecycle

    home, _ram = owner
    browser = FakeBrowser()
    _bind_browser(monkeypatch, browser)
    rec_mod.start("task-a")
    Path(browser.capture).write_bytes(VIDEO)  # the live daemon wrote before revocation
    vault.lock_now(home)
    vault.unlock(home, PASSWORD)

    assert rec_mod.stop("task-a") is None
    browser_tool_lifecycle.cleanup_browser("task-a")  # retry path uses the ORIGINAL custody

    assert _videos(home) == []
    assert Path(browser.capture).read_bytes() == VIDEO  # only copy retained in RAM
    assert rec_mod._RECORDINGS["task-a"].state == "unstopped"
    assert len(browser.stops()) == 0  # revoked callbacks cannot dispatch into a later unlock


def test_transient_seal_failure_is_retained_and_retried_by_cleanup(owner, monkeypatch):
    from hermes_security import artifacts
    from tools import browser_tool_lifecycle

    home, ram = owner
    browser = FakeBrowser()
    _bind_browser(monkeypatch, browser)
    real_write, failures = artifacts.write_bytes, []

    def flaky(*args, **kwargs):
        if Path(args[0]).parent.name == "videos" and not failures:
            failures.append(1)
            raise OSError("disk full")
        return real_write(*args, **kwargs)

    monkeypatch.setattr(artifacts, "write_bytes", flaky)
    rec_mod.start("task-a")
    assert rec_mod.stop("task-a") is None
    assert Path(browser.capture).read_bytes() == VIDEO

    browser_tool_lifecycle.cleanup_browser("task-a")

    [sealed] = _videos(home)
    assert artifacts.read_bytes(sealed, home=home) == VIDEO
    assert "task-a" not in rec_mod._RECORDINGS and list(ram.iterdir()) == []
    assert len(browser.stops()) == 1


def test_failed_stop_keeps_incomplete_capture_unpublished(owner, monkeypatch):
    home, _ram = owner
    browser = FakeBrowser(stop_ok=False)
    _bind_browser(monkeypatch, browser)
    rec_mod.start("task-a")
    Path(browser.capture).write_bytes(b"partial")

    assert rec_mod.stop("task-a") is None
    assert rec_mod.stop("task-a") is None

    assert _videos(home) == []
    assert Path(browser.capture).read_bytes() == b"partial"
    assert len(browser.stops()) == 1


def test_recording_publishes_to_original_home_and_child_close_spares_parent(owner, tmp_path, monkeypatch):
    from agent.secret_scope import reset_secret_scope, set_secret_scope
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_security import vault

    home, _ram = owner
    other = (tmp_path / "other").resolve()
    other.mkdir()
    vault.init_vault(other, PASSWORD)
    vault.unlock(other, PASSWORD)
    browser = FakeBrowser()
    _bind_browser(monkeypatch, browser)
    scope = set_secret_scope({"RECORDING_CONTEXT_TOKEN": "original"}, profile_home=str(home))
    try:
        rec_mod.start("parent")
        parent_capture = browser.capture
        rec_mod.start("child")
    finally:
        reset_secret_scope(scope)

    token = set_hermes_home_override(str(other))
    scope = set_secret_scope({"RECORDING_CONTEXT_TOKEN": "foreign"}, profile_home=str(other))
    try:
        child = rec_mod.stop("child")
    finally:
        reset_secret_scope(scope)
        reset_hermes_home_override(token)

    assert child is not None and child.is_relative_to(home)
    assert _videos(other) == []
    assert rec_mod._RECORDINGS["parent"].state == "recording"
    browser.capture = parent_capture
    assert rec_mod.stop("parent").is_relative_to(home)
    assert browser.contexts and set(browser.contexts) == {(home, "original")}


def test_retention_prunes_only_expired_current_recordings(owner, monkeypatch):
    from hermes_security import artifacts

    home, _ram = owner
    folder = home / "artifacts" / "media" / "videos"
    old_name = "browser_recording_20260101T000000Z_" + "a" * 32 + ".webm"
    new_name = "browser_recording_20260101T000000Z_" + "b" * 32 + ".webm"
    old = artifacts.write_bytes(folder / old_name, b"old", home=home)
    new = artifacts.write_bytes(folder / new_name, b"new", home=home)
    other = folder / "unrelated.webm"
    other.write_bytes(b"other")
    legacy = home / "browser_recordings" / "session_20250101_000000_task.webm"
    legacy.parent.mkdir()
    legacy.write_bytes(b"legacy plaintext only copy")
    stale = time.time() - 73 * 3600
    for path in (old, other, legacy):
        os.utime(path, (stale, stale))

    rec_mod.prune_expired(home, custody=artifacts._bound_custody(home))

    assert not old.exists()
    assert new.exists() and other.exists()
    assert legacy.read_bytes() == b"legacy plaintext only copy"


class FakeOutgoing:
    def __init__(self):
        self.files, self.removed = {}, []

    def fetch_file(self, remote, dest, *, max_bytes):
        data = self.files[remote]
        assert len(data) <= max_bytes
        Path(dest).write_bytes(data)


def test_outgoing_recording_uses_backend_ram_and_retires_only_allocated_capture(owner, monkeypatch):
    from hermes_security import artifacts

    home, ram = owner
    env = FakeOutgoing()
    runtime = BrowserRuntime("/run/user/1000/hb", "/run/user/1000/hb/profile", "/run/user/1000/hb/captures",
                             "/run/user/1000/hb/sockets", "/run/user/1000/hb/tmp")
    monkeypatch.setattr("tools.terminal_tool._get_env_config", lambda: {"env_type": "ssh"})
    monkeypatch.setattr("tools.browser_terminal.terminal_environment", lambda create=True: env)
    monkeypatch.setattr("tools.browser_terminal.browser_runtime", lambda _env: runtime)
    import subprocess
    monkeypatch.setattr("hermes_platform.remote_workspace.validate", lambda *args: None)
    monkeypatch.setattr("tools.environments.streams.run_in",
                        lambda _env, argv, timeout=None: (env.removed.append(argv[3:])
                                                        or subprocess.CompletedProcess(argv, 0)))
    monkeypatch.setattr(rec_mod, "_fetch_capture_bytes", lambda selected: env.files[selected.capture])
    browser = FakeBrowser(foreign=Path("/home/victim/secret.webm"), writes_local=False)
    _bind_browser(monkeypatch, browser)

    rec_mod.start("task-r")
    assert browser.capture.startswith(runtime.captures + "/")
    assert not Path(browser.capture).is_relative_to(ram)
    env.files[browser.capture] = VIDEO
    sealed = rec_mod.stop("task-r")

    assert artifacts.read_bytes(sealed, home=home) == VIDEO
    assert env.removed == [[browser.capture]]
    assert list(ram.iterdir()) == []


def test_refusing_backend_does_not_record_on_the_native_host(owner, monkeypatch):
    def refuse(_env):
        raise RuntimeError("no proven RAM-only directory")

    monkeypatch.setattr("tools.terminal_tool._get_env_config", lambda: {"env_type": "ssh"})
    monkeypatch.setattr("tools.browser_terminal.terminal_environment", lambda create=True: object())
    monkeypatch.setattr("tools.browser_terminal.browser_runtime", refuse)
    browser = FakeBrowser()
    _bind_browser(monkeypatch, browser)

    rec_mod.start("task-r")

    assert browser.calls == [] and "task-r" not in rec_mod._RECORDINGS
    assert list(owner[1].iterdir()) == []


def test_read_file_explains_video_recovery_without_decrypting(owner, monkeypatch):
    import json

    from hermes_security import artifacts
    from tools import private_artifact_files

    home, _ram = owner
    name = "browser_recording_20260101T000000Z_" + "c" * 32 + ".webm"
    sealed = artifacts.write_bytes(home / "artifacts" / "media" / "videos" / name, VIDEO, home=home)
    monkeypatch.setattr(artifacts, "read_bytes", lambda *a, **k: pytest.fail("video must not be decrypted"))

    result = json.loads(private_artifact_files.read_file(str(sealed), 1, 100, 10_000))

    assert result["is_binary"] is True
    assert "secure-vault export" in result["error"]
    assert "content" not in result or not result["content"]


def test_video_redirect_refuses_locked_missing_and_unadmitted_paths(owner):
    from hermes_security import artifacts, vault
    from hermes_security.errors import VaultError
    from tools import private_artifact_files

    home, _ = owner
    name = "browser_recording_20260101T000000Z_" + "d" * 32 + ".webm"
    path = artifacts.write_bytes(home.joinpath(*rec_mod.VIDEO_SUBDIR) / name, VIDEO, home=home)
    vault.lock_now(home)
    with pytest.raises(VaultError):
        private_artifact_files.read_file(str(path), 1, 100, 1000)
    vault.unlock(home, PASSWORD)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        private_artifact_files.read_file(str(path), 1, 100, 1000)
    unknown = path.parent / "not-a-recording.webm"
    unknown.write_bytes(b"unknown private video")
    with pytest.raises(PermissionError, match="admitted"):
        private_artifact_files.read_file(str(unknown), 1, 100, 1000)


def test_legacy_recordings_keep_their_path_bound_encryption_and_binary_recovery(owner):
    import json
    from hermes_security import artifacts, io, migrate
    from tools import private_artifact_files

    home, _ = owner
    relative = Path("browser_recordings/session_20250101_000000_default::local.webm")
    path = home / relative
    path.parent.mkdir()
    path.write_bytes(VIDEO)
    report = migrate.repair_clobbered_state(home)
    assert relative.as_posix() in report["sealed"] and not report["unrecoverable"]
    assert VIDEO not in path.read_bytes()
    assert io.read_bytes(path, purpose="state") == VIDEO
    assert artifacts.read_bytes(path, home=home) == VIDEO
    result = json.loads(private_artifact_files.read_file(str(path), 1, 100, 1000))
    assert result["is_binary"] and "secure-vault export" in result["error"]
