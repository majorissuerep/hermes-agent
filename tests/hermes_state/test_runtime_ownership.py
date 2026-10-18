"""Local runtimes coexist, but state replacement never races a live runtime."""

from pathlib import Path
import subprocess
import sys

import pytest

from hermes_state.runtime_ownership import ProfileOwnership, exclusive_maintenance

pytestmark = pytest.mark.platforms("linux", "macos")


def _probe(home: Path, *, maintenance: bool) -> int:
    source = """
import sys
from pathlib import Path
from hermes_state.runtime_ownership import OwnershipConflict, ProfileOwnership, exclusive_maintenance
home = Path(sys.argv[1])
try:
    if sys.argv[2] == 'maintenance':
        with exclusive_maintenance([home]):
            pass
    else:
        owner = ProfileOwnership()
        owner.reserve([home])
        owner.close()
except OwnershipConflict:
    raise SystemExit(23)
"""
    return subprocess.run(
        [sys.executable, "-c", source, str(home), "maintenance" if maintenance else "runtime"],
        check=False, timeout=15, capture_output=True, text=True,
    ).returncode


def test_terminal_child_shares_profile_but_restore_waits_for_every_runtime(tmp_path):
    home = tmp_path / "profile"
    parent, sibling = ProfileOwnership(), ProfileOwnership()
    try:
        parent.reserve([home])
        sibling.reserve([home])
        inode = (home / "runtime.lock").stat().st_ino
        assert _probe(home, maintenance=False) == 0
        assert _probe(home, maintenance=True) == 23
        parent.close()
        assert _probe(home, maintenance=True) == 23
        sibling.close()
        with exclusive_maintenance([home]):
            assert _probe(home, maintenance=False) == 23
        assert _probe(home, maintenance=True) == 0
        assert (home / "runtime.lock").stat().st_ino == inode
    finally:
        sibling.close()
        parent.close()


def test_upgrade_respects_existing_lock_without_replacing_its_inode(tmp_path):
    import fcntl

    home = tmp_path / "legacy"
    home.mkdir()
    legacy = home / "gateway.lock"
    with legacy.open("wb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        inode = legacy.stat().st_ino
        assert _probe(home, maintenance=False) == 23
        assert _probe(home, maintenance=True) == 23
    assert _probe(home, maintenance=False) == 0
    assert legacy.stat().st_ino == inode
