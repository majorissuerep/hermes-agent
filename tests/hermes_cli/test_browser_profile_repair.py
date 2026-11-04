"""Legacy raw browser profiles move into encrypted storage only through explicit offline repair.

Older builds kept the real-profile copy (``browser-profile/<browser>/``) and the ``/browser connect``
debug profile (``chrome-debug/``) as plaintext Chromium state under the home. ``secure-vault
migrate``/``repair`` must archive each one, prove an actual restore is byte-identical, and only then
retire it — never seal Chromium's files one by one (which breaks the profile) and never touch a
profile a browser or a running Hermes session still holds. The RAM proof is the labelled unit seam.
"""
from __future__ import annotations

import os
import socket
import types
from pathlib import Path

import pytest

from hermes_cli import browser_profile_store as store
from hermes_security import browser_profile_archive as archive
from hermes_security import migrate
from tests.hermes_cli.browser_profile_env import (
    PASSWORD, chromium_tree, cookie_db, owned_profile_env, plaintext_hits, reset_owned_profiles)


@pytest.fixture
def env(tmp_path, monkeypatch):
    yield owned_profile_env(tmp_path, monkeypatch)
    reset_owned_profiles()


def _legacy_real_copy(home):
    copy = chromium_tree(home / "browser-profile" / "chrome", "real-copy-cookie", stale_lock=False)
    (copy / ".hermes-snapshot-complete").write_text("Profile 2", encoding="utf-8")
    return copy


def test_repair_archives_verifies_and_retires_legacy_profiles(env, capsys):
    from hermes_cli.vault_cmd import cmd_vault

    home, _ram = env
    debug = chromium_tree(home / "chrome-debug", "debug-cookie", stale_lock=False)
    _legacy_real_copy(home)

    assert cmd_vault(types.SimpleNamespace(vault_command="repair")) == 0
    assert "Moved 2 plaintext browser profile(s)" in capsys.readouterr().out
    assert not debug.exists() and not (home / "browser-profile" / "chrome").exists()
    assert plaintext_hits(home, b"debug-cookie") == [] and plaintext_hits(home, b"real-copy-cookie") == []
    assert migrate.scan_for_plaintext(home) == []

    with store.prepare(store.owned_profile(archive.DEBUG_SLOT), {}) as restored:  # actual recovery
        assert cookie_db(restored / "Default" / "Network" / "Cookies") == "debug-cookie"


@pytest.mark.parametrize("hazard", ["live-browser", "running-hermes", "no-ram"])
def test_repair_leaves_legacy_profile_exactly_in_place_when_unsafe(tmp_path, monkeypatch, hazard):
    from hermes_state.runtime_ownership import ProfileOwnership

    home, _ram = owned_profile_env(tmp_path, monkeypatch, proven=hazard != "no-ram")
    debug = chromium_tree(home / "chrome-debug", "debug-cookie", stale_lock=False)
    if hazard == "live-browser":
        os.symlink(f"{socket.gethostname()}-{os.getpid()}", debug / "SingletonLock")
    before = archive.tree_digest(debug)
    session = ProfileOwnership()
    if hazard == "running-hermes":
        session.reserve([home])  # a cooperating local runtime of this profile
    try:
        report = migrate.repair_clobbered_state(home)
    finally:
        session.close()
        reset_owned_profiles()

    reason = {"live-browser": "still running", "running-hermes": "busy", "no-ram": "no proven RAM"}[hazard]
    assert report["browser_archived"] == [] and report["browser_retained"][0].startswith("chrome-debug (")
    assert reason in report["browser_retained"][0]
    assert archive.tree_digest(debug) == before and archive.archive_paths(home) == []
    assert cookie_db(debug / "Default" / "Network" / "Cookies") == "debug-cookie"  # never envelope-wrapped


def test_scan_reports_each_legacy_profile_once(env):
    home, _ram = env
    debug = chromium_tree(home / "chrome-debug", "debug-cookie")  # foreign-host lock: not convertible

    assert migrate.scan_for_plaintext(home) == ["chrome-debug"]
    assert migrate.repair_clobbered_state(home)["browser_retained"]
    assert cookie_db(debug / "Default" / "Network" / "Cookies") == "debug-cookie"


@pytest.mark.platforms("posix")
def test_legacy_repair_preserves_an_unrecognized_symlink_instead_of_silently_dropping_it(env):
    home, _ram = env
    debug = chromium_tree(home / "chrome-debug", "debug-cookie", stale_lock=False)
    link = debug / "Default" / "Login Data"
    link.symlink_to("Network/Cookies")
    original = (debug / "Default" / "Network" / "Cookies").read_bytes()

    report = migrate.repair_clobbered_state(home)
    assert report["browser_archived"] == []
    assert "Unsupported browser profile entry" in report["browser_retained"][0]
    assert link.is_symlink() and os.readlink(link) == "Network/Cookies"
    assert (debug / "Default" / "Network" / "Cookies").read_bytes() == original
    assert archive.archive_paths(home) == []


def test_scanner_reports_unverified_browser_publication_stages(env):
    home, _ram = env
    staged = home / "browser-profile" / ".stage-manual"
    staged.parent.mkdir(mode=0o700)
    staged.write_bytes(b"unsealed synthetic private cookie")

    assert migrate.scan_for_plaintext(home) == ["browser-profile/.stage-manual"]
    assert staged.read_bytes() == b"unsealed synthetic private cookie"


def test_partial_plaintext_retirement_reports_the_verified_recovery_archive(env, monkeypatch):
    from hermes_cli import browser_profile_repair

    home, ram = env
    debug = chromium_tree(home / "chrome-debug", "debug-cookie", stale_lock=False)
    original_remove = browser_profile_repair.shutil.rmtree

    def partial_remove(path, *args, **kwargs):
        if Path(path) == debug:
            (debug / "Local State").unlink()
            raise OSError("synthetic partial retirement")
        return original_remove(path, *args, **kwargs)

    monkeypatch.setattr(browser_profile_repair.shutil, "rmtree", partial_remove)
    report = migrate.repair_clobbered_state(home)
    assert report["browser_archived"] == []
    assert "verified encrypted recovery archive" in report["browser_retained"][0]
    assert "cleanup is incomplete" in report["browser_retained"][0]
    assert not (debug / "Local State").exists()  # the report never promises an unchanged raw tree
    restored = ram / "explicit-recovery"
    restored.mkdir(mode=0o700)
    archive.replay_archive(home, archive.ARCHIVE_ROOT + "/" + archive.DEBUG_SLOT + archive.ARCHIVE_SUFFIX,
                           custody=archive.capture_custody(home), target=restored)
    assert cookie_db(restored / "Default" / "Network" / "Cookies") == "debug-cookie"


def test_migration_converts_legacy_profiles_instead_of_sealing_chromium_files(tmp_path, monkeypatch):
    home, _ram = owned_profile_env(tmp_path, monkeypatch, vaulted=False)
    try:
        chromium_tree(home / "chrome-debug", "debug-cookie", stale_lock=False)

        report = migrate.migrate_home(home, PASSWORD)

        assert report.ok, report.failures
        assert report.browser_profiles == ["chrome-debug"] and not (home / "chrome-debug").exists()
        assert migrate.scan_for_plaintext(home) == []
        with store.prepare(store.owned_profile(archive.DEBUG_SLOT), {}) as restored:
            assert cookie_db(restored / "Default" / "Network" / "Cookies") == "debug-cookie"
    finally:
        reset_owned_profiles()
