"""Detached chat gateways keep the launcher's unlock across interpreter selection."""
import json
from pathlib import Path
import sys
import time

import pytest

from hermes_security import vault


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("reexec", [False, True])
def test_gateway_child_can_read_encrypted_config_without_a_terminal(tmp_path, monkeypatch, reexec):
    from hermes_cli import gateway_runtime_start as start
    from hermes_cli.config import save_config

    home = tmp_path / "state"
    monkeypatch.setenv("HERMES_HOME", str(home))
    for key in ("HERMES_ALLOW_NO_VAULT", "HERMES_SESSION_VAULT", "HERMES_VAULT_PRIVATE_KEY"):
        monkeypatch.delenv(key, raising=False)
    vault.init_vault(home, "synthetic-gateway-launch-password")
    vault.unlock(home, "synthetic-gateway-launch-password")
    save_config({"agent": {"max_turns": 37}}, merge_existing=True)
    witness = tmp_path / "witness.json"
    probe = tmp_path / "gateway_child.py"
    probe.write_text(f'''
import json, os, sys
from pathlib import Path
sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r})
from hermes_cli import venv_sync
from pm import environments
environments.activate_dependencies = lambda root: None
def select(root, argv):
    if {reexec!r} and not os.environ.get("GATEWAY_PROBE_REEXECED"):
        from hermes_security.handoff import adopt_inherited_key
        assert adopt_inherited_key()
        os.environ["GATEWAY_PROBE_REEXECED"] = "1"
        return Path(sys.executable)
venv_sync.prepare_launch = select
import hermes_bootstrap
from types import SimpleNamespace
from hermes_cli.vault_gate import gate_startup
gate_startup(SimpleNamespace(command="config"))
from hermes_cli.config import read_raw_config
Path({str(witness)!r}).write_text(json.dumps(read_raw_config()))
''')
    popen = start.subprocess.Popen

    def boundary(argv, **kwargs):
        # Keep the real detached env/fd/stdio boundary, replacing only the daemon's work.
        return popen([sys.executable, str(probe)], **kwargs)

    monkeypatch.setattr(start.subprocess, "Popen", boundary)
    try:
        child = start.spawn_unmanaged_gateway(home, deadline=time.monotonic() + 10)
        try:
            assert child.wait(timeout=30) == 0, (home / "logs/gateway-stdio.log").read_text()
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
        assert json.loads(witness.read_text())["agent"]["max_turns"] == 37
        assert (home / "config.yaml").read_bytes().startswith(b"HRMVAULT")
        assert "synthetic-gateway-launch-password" not in (home / "logs/gateway-stdio.log").read_text()
    finally:
        vault.clear_vault_cache()
