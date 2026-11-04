"""Source-install stages share only temporary, home-scoped unlock authority."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from hermes_security import vault


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("exit_code", [0, 7])
def test_install_stages_reuse_unlock_and_revoke_it_on_success_or_failure(tmp_path, monkeypatch, exit_code):
    from hermes_cli.config import read_raw_config, save_config
    from hermes_cli.source_install_session import run_install_session
    from hermes_security import session_authority

    home = tmp_path / "encrypted-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_ALLOW_NO_VAULT", raising=False)
    vault.init_vault(home, "synthetic-install-password")
    vault.unlock(home, "synthetic-install-password")
    save_config({"agent": {"max_turns": 19}}, merge_existing=False)
    observed = []
    start = session_authority.start_session_authority

    def capture(*args):
        authority = start(*args)
        observed.append(authority)
        return authority

    monkeypatch.setattr(session_authority, "start_session_authority", capture)
    # Qualify the public command and real native transport without running the
    # launcher's source-repair bootstrap against the working checkout.
    monkeypatch.setattr("hermes_cli._launchers.runtime_command",
                        lambda root, args: [sys.executable, "-m", "hermes_cli.main", *args])
    child = """
import json, os, subprocess, sys
from hermes_cli.update_vault import adopt_keys
adopt_keys()
from hermes_cli.config import read_raw_config, save_config
assert read_raw_config()['agent']['max_turns'] == 19
save_config({'agent': {'max_turns': 29}}, merge_existing=True)
assert 'HERMES_MASTER_PASSWORD' not in os.environ
probe = '''
import json, os
from hermes_security.handoff import adopt_inherited_key
from hermes_security import vault
from hermes_constants import get_hermes_home
from hermes_security.session_authority import _request_key
assert not adopt_inherited_key()
assert not vault.is_unlocked(get_hermes_home())
assert 'key' not in _request_key(json.loads(os.environ['HERMES_SESSION_VAULT']))
'''
assert subprocess.run([sys.executable, '-c', probe], check=False, timeout=30).returncode == 0
result = subprocess.run([sys.executable, '-m', 'hermes_cli.main', 'config', 'get', 'agent.max_turns'],
                        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False)
assert result.returncode == 0, result.stderr
assert '29' in result.stdout
raise SystemExit(int(sys.argv[1]))
"""
    try:
        assert run_install_session([sys.executable, "-c", child, str(exit_code)]) == exit_code
        assert read_raw_config()["agent"]["max_turns"] == 29
        assert (home / "config.yaml").read_bytes().startswith(b"HRMVAULT\x00")
        assert len(observed) == 1
        authority = observed[0]
        assert session_authority.session_environment(home, authority.session_id) == {}
        env = {**os.environ, **authority.environment()}
        independent = subprocess.run(
            [sys.executable, "-m", "hermes_cli.main", "config", "get", "agent.max_turns"],
            env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False,
            cwd=Path(__file__).resolve().parents[2],
        )
        assert independent.returncode != 0
        assert "session command refused" in (independent.stdout + independent.stderr).casefold()
    finally:
        vault.lock_now(home)


@pytest.mark.platforms("posix")
def test_failed_install_spawn_closes_key_descriptor_and_revokes_its_authority(tmp_path, monkeypatch):
    from hermes_cli.source_install_session import run_install_session
    from hermes_security import session_authority

    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    vault.init_vault(home, "synthetic-install-password")
    vault.unlock(home, "synthetic-install-password")
    observed = {}

    def fail_spawn(*args, env, **kwargs):
        observed["descriptor"] = int(env["HERMES_VAULT_KEY_FD"])
        observed["session"] = env["HERMES_SESSION_ID"]
        os.fstat(observed["descriptor"])
        raise OSError("synthetic spawn refusal")

    monkeypatch.setattr("hermes_platform.managed_supervision.subprocess.Popen", fail_spawn)
    try:
        with pytest.raises(OSError, match="synthetic spawn refusal"):
            run_install_session([sys.executable, "-c", "raise SystemExit(0)"])
        with pytest.raises(OSError):
            os.fstat(observed["descriptor"])
        assert session_authority.session_environment(home, observed["session"]) == {}
        assert vault.is_unlocked(home)
    finally:
        vault.lock_now(home)


@pytest.mark.platforms("posix")
def test_stage_control_executes_in_owner_and_returns_only_an_exit_receipt(tmp_path, monkeypatch):
    from hermes_cli.config import save_config
    from hermes_cli.source_install_session import run_install_session

    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    vault.init_vault(home, "synthetic-install-password")
    vault.unlock(home, "synthetic-install-password")
    save_config({"agent": {"max_turns": 27}}, merge_existing=False)
    stage = """
import os
from hermes_security.handoff import adopt_inherited_key
assert '__HERMES_INSTALL_OWNER_FD' not in os.environ
assert adopt_inherited_key()
from hermes_cli.config import read_raw_config
assert read_raw_config()['agent']['max_turns'] == 27
raise SystemExit(7)
"""
    # Only the owning parent chooses this inert real native consumer. The
    # ordinary Bash child below can request a stage, but cannot select code.
    monkeypatch.setattr("hermes_cli.source_install_stages._stage_command",
                        lambda arguments: [sys.executable, "-c", stage])
    client = """
import array, json, os, socket
owner = socket.socket(fileno=int(os.environ['__HERMES_INSTALL_OWNER_FD']))
reply, sender = socket.socketpair()
descriptors = array.array('i', [sender.fileno(), 0, 1, 2])
owner.sendmsg([json.dumps(['config']).encode()], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, descriptors)])
sender.close()
reply.settimeout(10)
data, ancillary, flags, _ = reply.recvmsg(512, socket.CMSG_SPACE(16))
assert not ancillary and not flags
assert json.loads(data) == {'returncode': 7}
reply.close()
owner.close()
"""
    try:
        assert run_install_session(["bash", "-c", 'exec "$1" -c "$2"', "installer-wire-test",
                                    sys.executable, client], stages=True) == 0
        assert vault.is_unlocked(home)
    finally:
        vault.lock_now(home)
