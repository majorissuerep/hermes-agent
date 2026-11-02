"""Concurrent local pipe grants retain encryption, exact-home scope and revocation."""
from concurrent.futures import ThreadPoolExecutor
import json
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
