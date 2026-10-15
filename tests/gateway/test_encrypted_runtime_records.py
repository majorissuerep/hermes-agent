"""Encrypted ownership records preserve the OS lock instead of replacing its inode."""
import os
import subprocess
import sys

import pytest

from hermes_security import vault


@pytest.mark.platforms("posix")
def test_encrypted_profile_ownership_and_pid_records_keep_exclusion(tmp_path, monkeypatch):
    from gateway.runtime_ownership import ProfileOwnership
    from gateway.status import write_pid_file
    from gateway.status_records import read_json_file

    homes = [tmp_path / name for name in ("a", "b")]
    owner = ProfileOwnership()
    try:
        for home in homes:
            vault.init_vault(home, "synthetic-runtime-record-password")
            vault.unlock(home, "synthetic-runtime-record-password")
        owner.reserve(homes)
        inodes = [(home / "gateway.lock").stat().st_ino for home in homes]
        for home in (homes[0], homes[1], homes[0]):
            monkeypatch.setenv("HERMES_HOME", str(home))
            write_pid_file()
            for name in ("gateway.lock", "gateway.pid"):
                path = home / name
                assert path.read_bytes().startswith(b"HRMVAULT")
                assert read_json_file(path)["pid"] == os.getpid()
            (home / "gateway.pid").unlink()
            child = subprocess.run([sys.executable, "-c", """
from pathlib import Path
import sys
from gateway.runtime_ownership import ProfileOwnership, OwnershipConflict
owner = ProfileOwnership()
try:
    owner.reserve([Path(sys.argv[1])])
except OwnershipConflict:
    print('excluded')
else:
    raise AssertionError('a second process acquired the held inode')
finally:
    owner.close()
""", str(home)], check=False, env=dict(os.environ, HERMES_HOME=str(home)),
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
            assert child.returncode == 0 and child.stdout.strip() == "excluded", child.stderr
            owner.reserve([home])
        assert [(home / "gateway.lock").stat().st_ino for home in homes] == inodes
    finally:
        owner.close()
        vault.clear_vault_cache()
