"""Disconnect checkpoints quiescent owned profiles without renewing or borrowing custody.

These are helper contracts with real vault/archive I/O. Only the unrelated browser-tool cleanup
is stubbed; the RAM proof is the existing labelled unit seam, not native browser evidence.
"""
from __future__ import annotations

import os
import secrets
import socket
import sys
from types import SimpleNamespace

import pytest

from hermes_cli import browser_profile_store as store
from hermes_security import browser_profile_archive as archive
from hermes_security import vault
from tests.hermes_cli.browser_profile_env import (
    PASSWORD, cookie_db, exited_browser, owned_profile_env, reset_owned_profiles,
)
from tui_runtime import server

IDENTITY = {"kind": "owned-debug-profile"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "tools.browser_tool_lifecycle",
                        SimpleNamespace(cleanup_all_browsers=lambda: None))
    yield owned_profile_env(tmp_path, monkeypatch)
    reset_owned_profiles()


def _prepared(cookie):
    profile = store.owned_profile(archive.DEBUG_SLOT)
    with store.prepare(profile, IDENTITY) as path:
        cookie_db(path / "Default" / "Network" / "Cookies", cookie)
        store.record_launch(profile, exited_browser())
    return profile, path


def test_disconnect_saves_and_retires_closed_profile_with_authenticated_cold_recovery(env):
    home, _ram = env
    private = secrets.token_hex(24)
    profile, path = _prepared(private)

    reply = server._browser_disconnect("disconnect")["result"]

    assert reply == {"connected": False}
    assert not path.exists()
    [stored] = archive.archive_paths(home)
    assert private.encode() not in stored.read_bytes()
    with store.prepare(profile, IDENTITY) as restored:
        assert cookie_db(restored / "Default" / "Network" / "Cookies") == private
        assert store._owned_binding(profile, restored)["restored"] is True


@pytest.mark.parametrize("state", ["busy", "revoked"])
def test_disconnect_reports_unsaved_original_copy_and_does_not_renew_it(env, state):
    home, _ram = env
    private = secrets.token_hex(24)
    _profile, path = _prepared(private)
    if state == "busy":
        os.symlink(f"{socket.gethostname()}-{os.getpid()}", path / "SingletonLock")
    else:
        vault.lock_now(home)
        vault.unlock(home, PASSWORD)  # a later unlock must not revive the original lease

    reply = server._browser_disconnect("disconnect")["result"]

    assert reply["connected"] is False
    assert any(state in message for message in reply["messages"])
    assert path.is_dir() and archive.archive_paths(home) == []
    assert cookie_db(path / "Default" / "Network" / "Cookies") == private


def test_disconnect_never_checkpoints_another_profile_or_an_ancestor(env, tmp_path, monkeypatch):
    home_a, _ram = env
    _profile_a, path_a = _prepared("profile-a-private-cookie")
    home_b = tmp_path / "profile-b"
    home_b.mkdir()
    vault.init_vault(home_b, PASSWORD)
    vault.unlock(home_b, PASSWORD)
    monkeypatch.setenv("HERMES_HOME", str(home_b))
    _profile_b, path_b = _prepared("profile-b-private-cookie")
    binding_b = (path_b / archive.RUNTIME_BINDING_NAME).read_bytes()

    # A selected missing nested profile must not fall back to A's existing authority or lease.
    monkeypatch.setenv("HERMES_HOME", str(home_a / "profiles" / "missing"))
    server._browser_disconnect("missing")
    assert path_a.exists() and archive.archive_paths(home_a) == []
    assert path_b.exists() and archive.archive_paths(home_b) == []

    monkeypatch.setenv("HERMES_HOME", str(home_a))
    server._browser_disconnect("profile-a")
    assert not path_a.exists() and len(archive.archive_paths(home_a)) == 1
    assert path_b.is_dir() and archive.archive_paths(home_b) == []
    assert (path_b / archive.RUNTIME_BINDING_NAME).read_bytes() == binding_b
    assert vault.is_unlocked(home_a) and vault.is_unlocked(home_b)
