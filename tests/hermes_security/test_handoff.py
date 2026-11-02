"""Vault handoff to a detached child (``hermes_security/handoff.py``): the child ends up able to read what the
parent sealed, the key crosses only an inherited pipe (never env/argv), and a forged key is refused."""

import os
import subprocess
import sys

import pytest

from hermes_security import vault as vault_mod
from hermes_security.errors import WrongMasterPasswordError

_CHILD = """
import os, sys
from hermes_security.handoff import KEY_FD_ENV, adopt_inherited_key
from hermes_security import vault as vault_mod
assert adopt_inherited_key() is True
assert KEY_FD_ENV not in os.environ  # consumed exactly once
v = vault_mod.get_vault(sys.argv[1], allow_env_unlock=False)
sys.stdout.write(v.read_bytes(os.path.join(sys.argv[1], "secret.bin"), purpose="test").decode())
"""


@pytest.mark.platforms("linux")  # pass_fds inheritance; the Windows host launcher does not hand off (documented)
def test_child_reads_parent_sealed_state_via_inherited_fd(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    vault_mod.init_vault(home, "correct horse")
    parent_vault = vault_mod.unlock(home, "correct horse")
    parent_vault.write_bytes(home / "secret.bin", b"sealed by the parent", purpose="test")
    monkeypatch.delenv("HERMES_MASTER_PASSWORD", raising=False)

    from hermes_security.handoff import prepare_child_key

    read_fd, extra_env = prepare_child_key()
    try:
        env = {**os.environ, **extra_env}
        assert "correct horse" not in "".join(env.values())
        out = subprocess.run([sys.executable, "-c", _CHILD, str(home)], check=False, env=env, pass_fds=(read_fd,),
                             capture_output=True, text=True, timeout=60)
    finally:
        os.close(read_fd)
        vault_mod.clear_vault_cache()
    assert out.returncode == 0, out.stderr
    assert out.stdout == "sealed by the parent"


def test_forged_key_is_refused(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    vault_mod.init_vault(home, "correct horse")
    vault_mod.clear_vault_cache()
    with pytest.raises(WrongMasterPasswordError):
        vault_mod.adopt_unlocked_key(home, os.urandom(32))
    assert not vault_mod.is_unlocked(home)


@pytest.mark.platforms("linux", "macos")
def test_local_handoff_carries_only_the_selected_profiles_key(tmp_path, monkeypatch):
    from hermes_security.handoff import prepare_child_key

    homes = [tmp_path / name for name in ("selected", "sibling")]
    monkeypatch.setenv("HERMES_HOME", str(homes[0]))
    for home in homes:
        vault_mod.init_vault(home, "synthetic-handoff-password")
        vault_mod.unlock(home, "synthetic-handoff-password")
    handoff = prepare_child_key(homes[0])
    assert handoff is not None
    fd, additions = handoff
    script = """
from hermes_security.handoff import adopt_inherited_key
from hermes_security.vault import is_unlocked
import sys
assert adopt_inherited_key()
assert is_unlocked(sys.argv[1])
assert not is_unlocked(sys.argv[2])
print('exact-profile-handoff')
"""
    try:
        result = subprocess.run([sys.executable, "-c", script, *map(str, homes)], env={**os.environ, **additions},
                                pass_fds=(fd,), capture_output=True, text=True, timeout=20, check=False)
        assert result.returncode == 0 and result.stdout.strip() == "exact-profile-handoff", result.stderr
        assert prepare_child_key(tmp_path / "unrelated") is None
    finally:
        os.close(fd)
        vault_mod.clear_vault_cache()
