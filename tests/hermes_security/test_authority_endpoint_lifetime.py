"""An idle session's real IPC nodes survive the ordinary scratch age/prune cycle."""
import json
import os
from pathlib import Path
import stat
import tempfile
import time

import pytest

pytestmark = pytest.mark.platforms("linux", "macos")


@pytest.mark.parametrize("pipe", [False, True])
def test_live_authority_nodes_survive_aged_scratch_pruning(monkeypatch, pipe):
    from hermes_constants import get_scratch_dir, prune_scratch_dir, set_hermes_home_override, reset_hermes_home_override
    from hermes_security import vault
    from hermes_security.session_authority import ENV_NAME, operation_request, start_session_authority

    # A short real home makes the former socket-safe TMPDIR choose scratch on Linux.
    with tempfile.TemporaryDirectory(prefix="hap-", dir="/tmp") as selected:  # no-tmp: ok -- short native AF_UNIX regression home
        home = Path(selected)
        monkeypatch.setenv("HERMES_HOME", str(home))
        token = set_hermes_home_override(home)
        scratch = get_scratch_dir(home, prune=False)
        monkeypatch.setattr(tempfile, "tempdir", str(scratch))
        for name in ("TMPDIR", "TMP", "TEMP"):
            monkeypatch.setenv(name, str(scratch))
        vault.init_vault(home, "synthetic-idle-authority-password")
        vault.unlock(home, "synthetic-idle-authority-password")
        authority = start_session_authority(home, "idle-owner", pipe=pipe)
        grant = authority.environment()[ENV_NAME]
        address = json.loads(grant)
        directory = Path(address["pipe"]) if pipe else Path(address["socket"]).parent
        idle = scratch / "unrelated-idle"
        idle.mkdir()
        (idle / "metadata").write_bytes(b"public synthetic metadata")
        identities = {path.name: (path.stat().st_dev, path.stat().st_ino) for path in directory.iterdir()}
        ancient = time.time() - 30 * 3600
        try:
            for root in (directory, idle):
                for path in (*root.rglob("*"), root):
                    os.utime(path, (ancient, ancient))
            assert prune_scratch_dir(scratch) >= 1
            assert not idle.exists()
            survived = directory.is_dir()
            assert survived, "scratch pruning removed a live session's IPC nodes"
            assert stat.S_IMODE(directory.stat().st_mode) == 0o700
            assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in directory.iterdir())
            assert len(os.fsencode(directory / "unlock.sock")) < 104
            assert identities == {path.name: (path.stat().st_dev, path.stat().st_ino) for path in directory.iterdir()}
            assert operation_request(home, grant, {"op": "check"})["live"] is True
        finally:
            authority.close()
            vault.clear_vault_cache()
            reset_hermes_home_override(token)


@pytest.mark.parametrize("pipe", [False, True])
def test_deep_home_and_temp_environment_do_not_lengthen_or_rehome_native_ipc(tmp_path, monkeypatch, pipe):
    from hermes_security import vault
    from hermes_security.session_authority import ENV_NAME, operation_request, start_session_authority

    home = tmp_path / ("deep-profile-" * 8) / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    scratch = home / "cache" / "scratch"
    scratch.mkdir(parents=True)
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    monkeypatch.setenv("TMPDIR", str(scratch))
    vault.init_vault(home, "synthetic-deep-authority-password")
    vault.unlock(home, "synthetic-deep-authority-password")
    authority = start_session_authority(home, "deep-owner", pipe=pipe)
    raw = authority.environment()[ENV_NAME]
    grant = json.loads(raw)
    directory = Path(grant["pipe"]) if pipe else Path(grant["socket"]).parent
    try:
        assert len(os.fsencode(directory / "unlock.sock")) < 104
        assert not directory.is_relative_to(home)
        assert operation_request(home, raw, {"op": "check"})["live"] is True
    finally:
        authority.close()
        vault.clear_vault_cache()
    assert not directory.exists()
