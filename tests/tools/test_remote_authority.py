"""Local session custody must not follow credential helpers or outgoing backends."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

AUTHORITY = ("HERMES_SESSION_VAULT", "HERMES_VAULT_KEY_FD", "HERMES_VAULT_PRIVATE_KEY",
             "HERMES_MASTER_PASSWORD", "__HERMES_INSTALL_KEYS_FD")


@pytest.mark.parametrize("factory", ["remote", "credential-helper"])
def test_unrelated_child_never_inherits_local_vault_authority(monkeypatch, factory):
    from tools.environments.local import build_subprocess_env
    from tools.environments.remote_common import client_env_with

    for name in AUTHORITY:
        monkeypatch.setenv(name, "synthetic-local-authority")
    monkeypatch.setenv("USER_OWN_CREDENTIAL", "user-owned")
    env = (client_env_with({}) if factory == "remote" else
           build_subprocess_env(scrub_secrets=False, inherit_profile_home=False))
    script = "import json,os; print(json.dumps({k:os.environ.get(k) for k in " + repr(AUTHORITY) + "}))"
    child = subprocess.run([sys.executable, "-c", script], env=env, text=True, capture_output=True,
                           check=True, timeout=10)
    assert all(value is None for value in json.loads(child.stdout).values())
    assert env["USER_OWN_CREDENTIAL"] == "user-owned"


def test_explicit_remote_forwarding_still_refuses_local_authority(monkeypatch):
    from tools.environments.remote_common import client_env_with, resolve_passthrough_env

    for name in AUTHORITY:
        monkeypatch.setenv(name, "synthetic-local-authority")
    monkeypatch.setenv("CUSTOM_REMOTE_KEY", "operator-selected")
    forwarded, unset = resolve_passthrough_env((*AUTHORITY, "CUSTOM_REMOTE_KEY"), hermes_env_loader=dict)
    assert forwarded == {"CUSTOM_REMOTE_KEY": "operator-selected"}
    assert set(AUTHORITY) <= unset
    client = client_env_with({**dict.fromkeys(AUTHORITY, "attempted-override"), **forwarded})
    assert not set(AUTHORITY).intersection(client)
    assert client["CUSTOM_REMOTE_KEY"] == "operator-selected"
