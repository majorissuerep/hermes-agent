"""Destructive profile operations refuse an actual native runtime owner."""
from pathlib import Path
import subprocess
import sys

import pytest

from hermes_cli import profiles
from hermes_state.runtime_ownership import OwnershipConflict

_HOLDER = (
    "import sys\nfrom pathlib import Path\n"
    "from hermes_state.runtime_ownership import ProfileOwnership\n"
    "owner=ProfileOwnership();owner.reserve([Path(sys.argv[1])])\n"
    "print('held',flush=True);sys.stdin.readline();owner.close()\n"
)


@pytest.mark.parametrize("operation", ["delete", "rename"])
def test_profile_home_is_not_moved_or_removed_while_owned(tmp_path, monkeypatch, operation):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    profile = profiles.create_profile("owned-delete", no_alias=True)
    holder = subprocess.Popen([sys.executable, "-c", _HOLDER, str(profile)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(OwnershipConflict):
            if operation == "delete":
                profiles.delete_profile("owned-delete", yes=True)
            else:
                profiles.rename_profile("owned-delete", "owned-renamed")
        assert profile.is_dir()
        assert (profile / "runtime.lock").is_file()
        assert holder.poll() is None
    finally:
        holder.stdin.write("\n")
        holder.stdin.flush()
        assert holder.wait(timeout=10) == 0
    if operation == "delete":
        profiles.delete_profile("owned-delete", yes=True)
    else:
        profiles.rename_profile("owned-delete", "owned-renamed")
    assert not profile.exists()
