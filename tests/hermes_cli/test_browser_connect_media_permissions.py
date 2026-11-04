"""The debug-browser launch stderr log is created and kept owner-only.

The ``/browser connect`` debug profile is a real Chromium user-data dir (Cookies, Login Data, Local
Storage). Its active copy now lives in the owner-only protected RAM workspace and is persisted only
as an encrypted archive (tests/hermes_cli/test_browser_profile_store.py). The launch stderr log is a
fixed, guessable name inside that copy (#77579 / #77486): it is created 0600, an older 0644 inode is
tightened before any bytes are written, and a planted link is never followed.
"""
import os
import stat

import pytest

import hermes_cli.browser_connect as bc


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_launch_stderr_log_owner_only(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    log = profile / bc._LAUNCH_STDERR_LOG
    previous = os.umask(0o022)
    try:
        with bc._open_launch_stderr_log(str(log)) as handle:
            handle.write(b"stderr tail")
    finally:
        os.umask(previous)
    assert _mode(log) == 0o600


def test_existing_lax_launch_stderr_log_is_tightened_and_truncated(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    log = profile / bc._LAUNCH_STDERR_LOG
    log.write_bytes(b"previous run")
    os.chmod(log, 0o644)
    with bc._open_launch_stderr_log(str(log)) as handle:
        handle.write(b"again")
    assert _mode(log) == 0o600
    assert log.read_bytes() == b"again"


@pytest.mark.platforms("posix")
def test_launch_stderr_log_never_follows_a_planted_link(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    target = tmp_path / "elsewhere.txt"
    target.write_bytes(b"untouched")
    os.symlink(target, profile / bc._LAUNCH_STDERR_LOG)
    with pytest.raises(OSError):
        bc._open_launch_stderr_log(str(profile / bc._LAUNCH_STDERR_LOG))
    assert target.read_bytes() == b"untouched"
