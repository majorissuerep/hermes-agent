"""Local session custody must not follow credential helpers or outgoing backends."""
from __future__ import annotations

import json
import os
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


@pytest.mark.platforms("linux", "macos")
@pytest.mark.parametrize("curated", [False, True])
def test_capture_helper_strips_authority_and_preserves_explicit_environment(monkeypatch, curated):
    from tools.environments.remote_common import run_capture

    names = (*AUTHORITY, *(name.lower() for name in AUTHORITY))
    for name in names:
        monkeypatch.setenv(name, "synthetic-local-authority")
    monkeypatch.setenv("ORDINARY_CLIENT_CREDENTIAL", "operator-selected")
    explicit = {**dict.fromkeys(names, "synthetic-override"), "CURATED_CLIENT_INPUT": "selected"} if curated else None
    script = ("import json,os; print(json.dumps({'authority':sum(k in os.environ for k in " + repr(names) + "),"
              "'ordinary':'ORDINARY_CLIENT_CREDENTIAL' in os.environ,'curated':'CURATED_CLIENT_INPUT' in os.environ}))")
    result = run_capture([sys.executable, "-c", script], timeout=10, check=True, env=explicit)
    assert json.loads(result.stdout) == {"authority": 0, "ordinary": not curated, "curated": curated}
    if curated:
        assert set(names) <= explicit.keys(), "stripping must not mutate the caller's curated environment"


@pytest.mark.platforms("linux", "macos")
@pytest.mark.parametrize("operation", ["ssh-setup", "scp-upload", "ssh-upload", "ssh-download", "ssh-cleanup",
                                       "ssh-command", "singularity-command"])
def test_backend_client_spawn_boundaries_strip_authority(tmp_path, monkeypatch, operation):
    """Real native clients with synthetic executables; this does not qualify remote execution."""
    from tools.environments.singularity import SingularityEnvironment
    from tools.environments.ssh import SSHEnvironment

    report = tmp_path / "client-observations.jsonl"
    directory = tmp_path / "client-bin"
    directory.mkdir()
    monkeypatch.setenv("PATH", str(directory) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("CLIENT_OBSERVATIONS", str(report))
    monkeypatch.setenv("ORDINARY_CLIENT_CREDENTIAL", "operator-selected")
    for name in AUTHORITY:
        monkeypatch.setenv(name, "synthetic-local-authority")
    probe = (f"#!{sys.executable}\nimport json,os,sys\n"
             f"state={{'authority':sum(k in os.environ for k in {AUTHORITY!r}),"
             "'ordinary':'ORDINARY_CLIENT_CREDENTIAL' in os.environ}\n"
             "with open(os.environ['CLIENT_OBSERVATIONS'], 'a') as stream:\n"
             "    stream.write(json.dumps(state) + '\\n')\n"
             "sys.stdin.buffer.read()\n")
    for name in ("ssh", "scp", "singularity"):
        executable = directory / name
        executable.write_text(probe, encoding="utf-8")
        executable.chmod(0o700)
    source = tmp_path / "payload"
    source.write_bytes(b"synthetic sync payload")
    if operation == "singularity-command":
        instance = object.__new__(SingularityEnvironment)
        instance.executable = str(directory / "singularity")
        instance.instance_id = "synthetic-instance"
        instance._instance_started = True
        child = instance._run_bash("synthetic-command")
        child.communicate(timeout=10)
        assert child.returncode == 0
    else:
        instance = object.__new__(SSHEnvironment)
        instance.host, instance.user, instance.port, instance.key_path = "synthetic.invalid", "fixture", 22, ""
        instance._remote_home, instance._sync_manager = "/synthetic", None
        instance.control_socket = tmp_path / "control.sock"
        instance.control_socket.touch()
        monkeypatch.setattr(instance, "_build_ssh_command", lambda **kwargs: [str(directory / "ssh")])

        def command():
            child = instance._run_bash("synthetic-command")
            child.communicate(timeout=10)
            assert child.returncode == 0

        handlers = {
            "ssh-setup": lambda: instance._run_ssh("synthetic-command", timeout=10).check_returncode(),
            "scp-upload": lambda: instance._scp_upload(str(source), "/synthetic/.hermes/payload"),
            "ssh-upload": lambda: instance._ssh_bulk_upload([(str(source), "/synthetic/.hermes/payload")]),
            "ssh-download": lambda: instance._ssh_bulk_download(tmp_path / "received"),
            "ssh-cleanup": instance.cleanup,
            "ssh-command": command,
        }
        handlers[operation]()
    observations = [json.loads(line) for line in report.read_text().splitlines()]
    assert observations and all(state == {"authority": 0, "ordinary": True} for state in observations)
