"""Native filesystem/argv helper contracts; these do not qualify live remote backends or RAM."""
import subprocess

import pytest

from hermes_platform.remote_identity import capture, guarded_argv


def _run(argv, **kwargs):
    return subprocess.run(argv, capture_output=True, check=False, **kwargs)


@pytest.mark.platforms("linux")
@pytest.mark.parametrize("changed", ["root", "child"])
def test_guard_rejects_owned_0700_replacement_after_validation(tmp_path, changed):
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    child = root / "capture"
    child.mkdir(mode=0o700)
    child.chmod(0o700)
    identity = capture(_run, [str(root), str(child)])
    command = guarded_argv(identity, ["printf", "executed"])
    assert _run(command, timeout=5).stdout == b"executed"
    selected = root if changed == "root" else child
    selected.rename(selected.with_name("original-inode"))
    selected.mkdir(mode=0o700)
    selected.chmod(0o700)
    if changed == "root":
        child.mkdir(mode=0o700)
        child.chmod(0o700)
    # Paths, account and permissions are all valid. The retained inode must still fence effects.
    replacement = capture(_run, [str(root), str(child)])
    assert replacement.epoch == identity.epoch and replacement != identity
    refused = _run(command, timeout=5)
    assert refused.returncode == 97 and refused.stdout == b""
    assert b"missing or changed" in refused.stderr
    assert _run(guarded_argv(replacement, ["printf", "new-owner"]), timeout=5).stdout == b"new-owner"
