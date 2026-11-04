"""Inherited self-management keeps its exact home despite another sticky human profile."""
import json
import secrets
import subprocess

import pytest

from tests.hermes_cli.test_management_public_cli import ROOT

pytest_plugins = ["tests.hermes_cli.test_management_public_cli"]
pytestmark = [pytest.mark.platforms("linux", "macos"), pytest.mark.live_system_guard_bypass]


def test_inherited_management_keeps_root_while_independent_human_keeps_sticky_profile(world):
    from hermes_cli._launchers import runtime_command
    from hermes_cli.config import save_config
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_security import vault
    from tests.e2e.vault_home import spawn_unlocked

    selected = world.home / "profiles" / "work"
    password = secrets.token_urlsafe(32)
    vault.init_vault(selected, password)
    vault.unlock(selected, password)
    token = set_hermes_home_override(selected)
    try:
        save_config({"agent": {"max_turns": 61}, "updates": {"check": False}}, strip_defaults=False)
    finally:
        reset_hermes_home_override(token)
    (world.home / "active_profile").write_text("work\n")
    request = {"op": "config.get", "path": ["agent", "max_turns"]}
    result, output = world.call(request)
    assert result.returncode == 0 and output["result"]["value"] == 19, world.diagnostic(output)
    for profile in ("work", "default"):
        result, output = world.call(request, profile=profile)
        assert (result.returncode == 0) is (profile == "default"), world.diagnostic(output)
    # Native FD children keep the selected root both before and after consuming
    # their descriptor and removing their environment's operation grant.
    for consumed in (False, True):
        code = """
import os, runpy, sys
if sys.argv.pop(1) == 'consumed':
    from hermes_constants import get_hermes_home
    from hermes_security.handoff import adopt_inherited_key
    from hermes_security.session_authority import has_borrowed_authority
    adopt_inherited_key()  # bootstrap may already have consumed this same FD
    assert has_borrowed_authority(get_hermes_home())
    os.environ.pop('HERMES_SESSION_VAULT', None)
    os.environ.pop('HERMES_SESSION_ID', None)
runpy.run_module('hermes_cli.main', run_name='__main__', alter_sys=True)
"""
        args = ["consumed" if consumed else "unconsumed", "manage", "apply"]
        with spawn_unlocked(runtime_command(ROOT, args, code=code), env=world.env, cwd=ROOT,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
            stdout, stderr = child.communicate(json.dumps(request), timeout=60)
        diagnostic = world.diagnostic((stdout, stderr))
        assert child.returncode == 0, diagnostic
        assert json.loads(stdout)["result"]["value"] == 19, diagnostic
    # An independently credential-unlocked process has no inherited authority;
    # its ordinary public CLI still selects the human's sticky work profile.
    code = """
import os, runpy, sys
from pathlib import Path
from hermes_security.vault import unlock
unlock(Path(os.environ['HERMES_HOME']) / 'profiles' / 'work', sys.stdin.readline().rstrip('\\n'))
runpy.run_module('hermes_cli.main', run_name='__main__', alter_sys=True)
"""
    env = {name: value for name, value in world.env.items()
           if name not in {"HERMES_SESSION_VAULT", "HERMES_VAULT_KEY_FD", "HERMES_SESSION_ID"}}
    result = subprocess.run(runtime_command(ROOT, ["manage", "apply"], code=code), env=env, cwd=ROOT,
                            input=password + "\n" + json.dumps(request), capture_output=True, text=True,
                            timeout=60, check=False)
    diagnostic = world.diagnostic((result.stdout, result.stderr), secret=password)
    assert result.returncode == 0, diagnostic
    assert json.loads(result.stdout)["result"]["value"] == 61, diagnostic
    assert vault.is_unlocked(world.home)
