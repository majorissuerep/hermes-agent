"""The chrome-debug Chromium profile and Hermes media-cache dirs are created owner-only.

``$HERMES_HOME/chrome-debug`` is a real Chromium user-data dir (Cookies, Login Data,
Local Storage). A bare ``os.makedirs`` inherited the umask and landed 0755, so on the
documented ``HERMES_HOME_MODE=0701`` web-server hatch every other local account could
read those stores (#77579 / #77486). (Media no longer lands in such caches: vision/video
prepare in memory, and computer-use captures are encrypted profile artifacts — see
tests/tools/test_computer_use_private_media.py.) These tests pin the creation contract:
0700 dirs at creation time and a 0600 launch stderr log, with managed (NixOS) installs
deliberately left to the configured umask/setgid.
"""
import os
import stat

import pytest

import hermes_cli.browser_connect as bc

pytestmark = pytest.mark.platforms("linux", "macos")


@pytest.fixture
def private_home(tmp_path):
    """Exact-0700 cases need a parent without an inherited SGID policy."""
    home = tmp_path / "hh"
    home.mkdir(mode=0o700)
    home.chmod(0o700)
    assert _mode(home) == 0o700
    return home


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _no_managed(monkeypatch, home):
    """Force the not-managed branch: no HERMES_MANAGED env, no marker in ``home``."""
    monkeypatch.delenv("HERMES_MANAGED", raising=False)
    monkeypatch.delenv("HERMES_HOME_MODE", raising=False)
    monkeypatch.delenv("HERMES_UID", raising=False)
    monkeypatch.delenv("HERMES_GID", raising=False)
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: home)


def test_chrome_debug_data_dir_created_owner_only(private_home, monkeypatch):
    """A fresh chrome-debug profile dir lands 0700, not the umask-derived 0755."""
    home = private_home
    _no_managed(monkeypatch, home)
    monkeypatch.setattr(bc, "get_hermes_home", lambda: home)
    bc._ensure_chrome_debug_data_dir(bc.chrome_debug_data_dir())
    assert _mode(home / "chrome-debug") == 0o700


def test_chrome_debug_data_dir_reconciles_explicit_owner_only_policy(private_home, monkeypatch):
    """A profile an older Hermes left at 0755 is tightened on the next launch — the
    exposure is already on disk, so creation-time hardening alone is not enough."""
    home = private_home
    _no_managed(monkeypatch, home)
    monkeypatch.setattr(bc, "get_hermes_home", lambda: home)
    profile = home / "chrome-debug"
    profile.mkdir(parents=True, mode=0o755)
    monkeypatch.setenv("HERMES_HOME_MODE", "0700")
    bc._ensure_chrome_debug_data_dir(str(profile))
    assert _mode(profile) == 0o700


def test_chrome_debug_data_dir_managed_install_leaves_mode_to_umask(private_home, monkeypatch):
    """Managed (NixOS) installs share $HERMES_HOME group-wise by design; the profile
    is not pre-created by tmpfiles rules, so the creation mode must stay with the
    configured umask/setgid rather than a hardcoded 0700."""
    home = private_home
    monkeypatch.setenv("HERMES_MANAGED", "nixos")
    monkeypatch.delenv("HERMES_HOME_MODE", raising=False)
    monkeypatch.setattr(bc, "get_hermes_home", lambda: home)
    monkeypatch.setattr(bc, "_managed_install", lambda: True)
    profile = home / "chrome-debug"
    bc._ensure_chrome_debug_data_dir(str(profile))
    # mkdir with no explicit mode: umask applies (0o777 & ~umask); the point is that
    # the helper did NOT force 0700 — group bits survive under the service umask.
    assert _mode(profile) == 0o777 & ~_umask()


def _umask() -> int:
    current = os.umask(0o022)
    os.umask(current)
    return current


def test_launch_stderr_log_owner_only(private_home, monkeypatch):
    """The launch stderr log (fixed, guessable name inside the profile dir) is created
    0600, and an older 0644 log is reconciled, not left exposed on upgrade."""
    home = private_home
    _no_managed(monkeypatch, home)
    monkeypatch.setattr(bc, "get_hermes_home", lambda: home)
    log = home / "chrome-debug" / bc._LAUNCH_STDERR_LOG
    log.parent.mkdir(parents=True, mode=0o700)
    with bc._open_launch_stderr_log(str(log)) as handle:
        handle.write(b"stderr tail")
    assert _mode(log) == 0o600


def test_legacy_launch_stderr_log_reconciles_on_bare_hosts(private_home, monkeypatch):
    import hermes_constants

    home = private_home
    _no_managed(monkeypatch, home)
    if hermes_constants._container_or_chmod_skipped():
        pytest.skip("container/shared-mount policy intentionally leaves existing file modes unchanged")
    monkeypatch.setattr(bc, "get_hermes_home", lambda: home)
    log = home / "chrome-debug" / bc._LAUNCH_STDERR_LOG
    log.parent.mkdir(parents=True, mode=0o700)
    log.write_bytes(b"previous run")
    os.chmod(log, 0o644)
    with bc._open_launch_stderr_log(str(log)) as handle:
        handle.write(b"again")
    assert _mode(log) == 0o600
    assert log.read_bytes() == b"again"


@pytest.mark.parametrize("managed", [False, True], ids=["private", "managed"])
def test_chrome_debug_preserves_native_sgid_policy_without_extra_private_access(tmp_path, monkeypatch, managed):
    import hermes_constants

    home = tmp_path / "group-home"
    home.mkdir()
    os.chown(home, -1, os.getegid())
    home.chmod(0o2700)
    assert _mode(home) == 0o2700
    _no_managed(monkeypatch, home)
    if managed:
        monkeypatch.setenv("HERMES_MANAGED", "nixos")
    monkeypatch.setattr(bc, "get_hermes_home", lambda: home)
    monkeypatch.setattr(bc, "_managed_install", lambda: managed)
    previous_umask = os.umask(0o022)
    try:
        bc._ensure_chrome_debug_data_dir(bc.chrome_debug_data_dir())
    finally:
        os.umask(previous_umask)
    profile = home / "chrome-debug"
    assert _mode(home) == 0o2700
    if managed:
        assert _mode(profile) == 0o2755
    else:
        assert _mode(profile) & 0o777 == 0o700
        expected = 0o2700 if hermes_constants._container_or_chmod_skipped() else 0o700
        assert _mode(profile) == expected
