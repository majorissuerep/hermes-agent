"""Native Kanban adoption is home-scoped and never extends a borrowed terminal grant."""
import json
import os
import secrets
from types import SimpleNamespace

import pytest


@pytest.fixture
def worker_env(tmp_path):
    from hermes_security import vault

    homes = [tmp_path / name for name in ("worker", "unrelated")]
    for home in homes:
        password = secrets.token_urlsafe(24)
        vault.init_vault(home, password)
        vault.unlock(home, password)
    try:
        yield {"HERMES_HOME": str(homes[0]), "HERMES_KANBAN_DB": str(homes[0] / "kanban/kanban.db"),
               "HERMES_KANBAN_TASK": "t_owned", "HERMES_KANBAN_RUN_ID": "7",
               "HERMES_KANBAN_CLAIM_LOCK": "owned-claim"}
    finally:
        for home in homes:
            vault.lock_now(home)


def test_finite_dispatch_adoption_is_encrypted_and_scoped(worker_env, monkeypatch):
    from hermes_cli.kanban_worker_authority import _prepare, _ready_path, _launched_worker, adopt_worker_execution
    from hermes_cli.kanban_db_dispatch import _process_fingerprint
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_security import io
    from hermes_security.session_authority import ENV_NAME

    authority = _prepare(worker_env)
    ready = _ready_path(worker_env)
    ready.parent.mkdir(parents=True, mode=0o700)
    for key, value in worker_env.items():
        monkeypatch.setenv(key, value)
    token = set_hermes_home_override(worker_env["HERMES_HOME"])
    adopted = None
    try:
        grant = json.loads(worker_env[ENV_NAME])
        assert grant["home"] == worker_env["HERMES_HOME"]
        assert "unrelated" not in worker_env[ENV_NAME]
        assert "HERMES_VAULT_KEY_FD" not in worker_env
        adopted = adopt_worker_execution()
        assert ready.read_bytes().startswith(b"HRMVAULT\x00")
        assert b"owned-claim" not in ready.read_bytes()
        receipt = io.read_json(ready, purpose="state")
        assert receipt["claim"] == "owned-claim" and receipt["run_id"] == 7
        assert receipt["pid"] > 0 and receipt["started_at"]
        launcher = SimpleNamespace(pid=os.getpid())
        assert _launched_worker(launcher, receipt["started_at"], receipt)
        unrelated = {**receipt, "pid": os.getppid(), "started_at": _process_fingerprint(os.getppid())}
        assert not _launched_worker(launcher, receipt["started_at"], unrelated)
        assert ready.stat().st_mode & 0o777 == 0o600
    finally:
        if adopted is not None:
            adopted.close()
        authority.close()
        reset_hermes_home_override(token)


def test_borrowed_terminal_grant_is_not_reissued(worker_env, monkeypatch):
    from agent.session_context import _VAR_MAP
    from hermes_cli.kanban_worker_authority import _prepare
    from hermes_security.session_authority import start_session_authority

    authority = start_session_authority(worker_env["HERMES_HOME"], "terminal-owner")
    worker_env.update(authority.environment())
    # The dispatcher strips routing ids; the active context, not launch-profile env,
    # must select the session that owns the retained grant.
    monkeypatch.setenv("HERMES_SESSION_ID", "unrelated-launch-session")
    token = _VAR_MAP["HERMES_SESSION_ID"].set("terminal-owner")
    try:
        original = dict(worker_env)
        assert _prepare(worker_env) is None
        assert worker_env == {**original, "HERMES_SESSION_ID": "terminal-owner"}
        assert authority.references == 1
        worker_env["HERMES_HOME"] += "-other"
        with pytest.raises(RuntimeError, match="does not authorize"):
            _prepare(worker_env)
    finally:
        _VAR_MAP["HERMES_SESSION_ID"].reset(token)
        authority.close()
