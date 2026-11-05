"""Shared profile resources stay live while each operation retains its original session."""
from pathlib import Path
import os
import subprocess
import sys

import pytest

from hermes_security import artifacts, vault
from hermes_security.errors import VaultLockedError
from hermes_security.session_authority import ENV_NAME, session_environment, start_session_authority
from hermes_security.session_custody import SessionCustody

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def home(tmp_path, monkeypatch):
    selected = tmp_path/'owner'
    selected.mkdir(mode=0o700)
    monkeypatch.setenv('HERMES_HOME', str(selected))
    monkeypatch.setattr(vault, '_SCRYPT_N', 1024)
    vault.init_vault(selected, 'synthetic operation password')
    vault.unlock(selected, 'synthetic operation password')
    yield selected
    vault.clear_vault_cache()


def test_each_operation_keeps_its_source_and_shared_resources_survive(home):
    from agent.session_context import scoped_current_session_id

    first = start_session_authority(home, 'A')
    sibling = start_session_authority(home, 'B')
    resource = artifacts._bound_custody(home)
    try:
        with scoped_current_session_id('A'):
            old = SessionCustody.capture(home, resource=resource)
        with scoped_current_session_id('B'):
            other = SessionCustody.capture(home, resource=resource)
        first.close()
        with scoped_current_session_id('B'):
            with pytest.raises(VaultLockedError):
                old.check()
            with pytest.raises(VaultLockedError):
                old.derive_key('private-operation-test')
            other.check()
            assert len(other.derive_key('private-operation-test')) == 32
        resource.check()
        with scoped_current_session_id('A'):
            with pytest.raises(VaultLockedError):
                SessionCustody.capture(home, resource=resource)
        with scoped_current_session_id(''):
            maintenance = SessionCustody.capture(home, resource=resource)
            maintenance.check()
        assert vault.is_unlocked(home) and session_environment(home, 'B')
        assert repr(old) == '<SessionCustody>'
        import pickle
        with pytest.raises(TypeError):
            pickle.dumps(old)
    finally:
        first.close()
        sibling.close()


@pytest.mark.platforms('posix')
def test_trusted_native_child_retains_upstream_session_without_local_issuer(home):
    from hermes_security.handoff import prepare_child_key

    parent = start_session_authority(home, 'native-parent')
    script = """
import sys
from pathlib import Path
from agent.session_context import scoped_current_session_id
from hermes_security.handoff import adopt_inherited_key
from hermes_security.session_custody import SessionCustody
assert adopt_inherited_key()
with scoped_current_session_id('native-parent'):
    operation = SessionCustody.capture(Path(sys.argv[1]))
operation.check()
print('ready', flush=True)
sys.stdin.readline()
from hermes_security.errors import VaultLockedError
try:
    operation.check()
except VaultLockedError:
    print('revoked', flush=True)
else:
    raise AssertionError('Borrowed native operation survived original issuer close')
"""
    fd, additions = prepare_child_key(home, source_grant=parent.environment()[ENV_NAME])
    try:
        env = {**os.environ, **additions}
        env.pop(ENV_NAME, None)  # authoritative provenance is the trusted FD, not child env
        with subprocess.Popen([sys.executable, '-c', script, str(home)], cwd=ROOT, env=env,
                              pass_fds=(fd,), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as child:
            import select
            try:
                assert select.select([child.stdout], [], [], 20)[0]
                assert child.stdout.readline().strip() == 'ready'
                parent.close()
                output, _error = child.communicate('check\n', timeout=20)
                assert child.returncode == 0 and output.strip() == 'revoked'
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
        assert vault.is_unlocked(home)
    finally:
        os.close(fd)
        parent.close()
