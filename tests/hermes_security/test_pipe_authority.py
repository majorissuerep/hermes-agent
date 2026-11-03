"""Concurrent local pipe grants retain encryption, exact-home scope and revocation."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import stat

import pytest

from tests.hermes_security.test_session_authority import child

pytestmark = pytest.mark.platforms("linux", "macos")


def test_pipe_clients_are_correlated_and_leave_no_regular_file_credentials(tmp_path, monkeypatch):
    from hermes_cli.config import save_config
    from hermes_security import vault
    from hermes_security.session_authority import ENV_NAME, start_session_authority

    home = tmp_path / "state"
    monkeypatch.setenv("HERMES_HOME", str(home))
    vault.init_vault(home, "synthetic-pipe-password")
    vault.unlock(home, "synthetic-pipe-password")
    save_config({"agent": {"max_turns": 41}}, strip_defaults=False)
    authority = start_session_authority(home, "pipe-owner", pipe=True)
    grant = authority.environment()
    directory = Path(json.loads(grant[ENV_NAME])["pipe"])
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: child(home, grant), range(8)))
        assert all(result.returncode == 0 for result in results), [r.stderr for r in results]
        assert all(json.loads(result.stdout)["agent"]["max_turns"] == 41 for result in results)
        regular = [p for p in directory.iterdir() if stat.S_ISREG(p.lstat().st_mode)]
        assert [path.name for path in regular] == ["lock"]
        assert regular[0].read_bytes() == b""
        assert (home / "config.yaml").read_bytes().startswith(b"HRMVAULT")
    finally:
        authority.close()
        vault.clear_vault_cache()
    assert not directory.exists()
    assert child(home, grant).returncode != 0


def test_refused_pipe_grant_does_not_poison_the_next_owner_request(tmp_path, monkeypatch):
    from hermes_cli.config import save_config
    from hermes_security import vault
    from hermes_security.session_authority import ENV_NAME, start_session_authority

    home = tmp_path / "state"
    monkeypatch.setenv("HERMES_HOME", str(home))
    vault.init_vault(home, "synthetic-pipe-password")
    vault.unlock(home, "synthetic-pipe-password")
    save_config({"agent": {"max_turns": 43}}, strip_defaults=False)
    authority = start_session_authority(home, "pipe-owner", pipe=True)
    grant = authority.environment()
    parsed = json.loads(grant[ENV_NAME])
    try:
        invalid = dict(parsed, token="not-the-issued-token")
        assert child(home, {ENV_NAME: json.dumps(invalid)}).returncode != 0
        assert child(home, grant).returncode == 0
        assert child(tmp_path / "foreign", grant).returncode != 0
        directory = Path(parsed["pipe"])
        (directory / "lock").chmod(0o644)
        assert child(home, grant).returncode != 0
        (directory / "lock").chmod(0o600)
        assert child(home, grant).returncode == 0
    finally:
        authority.close()
        vault.clear_vault_cache()


@pytest.mark.parametrize("replacement", ["file", "symlink", "shared_fifo", "directory"])
def test_reply_rejects_replaced_nodes_without_persisting_keys(tmp_path, monkeypatch, replacement):
    """Even a queued, valid request must not write a key through a replaced response node."""
    from hermes_cli.config import save_config
    from hermes_security import vault
    from hermes_security.session_authority import ENV_NAME, start_session_authority

    home = tmp_path / "state"
    monkeypatch.setenv("HERMES_HOME", str(home))
    vault.init_vault(home, "synthetic-pipe-password")
    vault.unlock(home, "synthetic-pipe-password")
    save_config({"agent": {"max_turns": 47}}, strip_defaults=False)
    authority = start_session_authority(home, "pipe-owner", pipe=True)
    grant = authority.environment()
    response = authority.server.path / "response"
    target = tmp_path / "unchanged"
    target.write_bytes(b"unchanged")
    reader = None
    try:
        response.unlink()
        if replacement == "file":
            response.write_bytes(b"unchanged")
            response.chmod(0o600)
        elif replacement == "symlink":
            response.symlink_to(target)
        elif replacement == "shared_fifo":
            os.mkfifo(response, 0o644)
            response.chmod(0o644)
            reader = os.open(response, os.O_RDONLY | os.O_NONBLOCK)
        else:
            response.mkdir()
        request = {**json.loads(grant[ENV_NAME]), "request_id": "queued-before-node-change"}
        authority.server._reply(json.dumps(request).encode())
        if reader is not None:
            leaked = bool(os.read(reader, 4096))
            assert not leaked, "An unprotected FIFO received a credential frame"
            os.close(reader)
            reader = None
        elif replacement != "directory":
            unchanged = response.read_bytes() == b"unchanged"
            assert unchanged, "A regular file received a credential frame"
        if replacement == "directory":
            response.rmdir()
        else:
            response.unlink()
        os.mkfifo(response, 0o600)
        assert child(home, grant).returncode == 0
        assert authority.thread.is_alive()
    finally:
        if reader is not None:
            os.close(reader)
        authority.close()
        vault.clear_vault_cache()
