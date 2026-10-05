"""Password-only vaults survive isolated preparation and selected-Python completion."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from hermes_security import io, vault


@pytest.fixture
def homes(tmp_path, monkeypatch):
    home = tmp_path / "home"
    sibling = home / "profiles/work"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for key in ("HERMES_MASTER_PASSWORD", "HERMES_VAULT_PRIVATE_KEY", "HERMES_VAULT_KEY_FD"):
        monkeypatch.delenv(key, raising=False)
    for target in (home, sibling):
        target.mkdir(parents=True)
        vault.init_vault(target, "only the parent knows this password")
        vault.unlock(target, "only the parent knows this password")
        io.write_text(target / "config.yaml", f"model: {target.name}-canary\n", purpose="config", encoding="utf-8")
    yield home, sibling
    vault.clear_vault_cache()


@pytest.mark.platforms("any")  # Inherited POSIX descriptors and native Windows pipe handles.
def test_preparation_uses_owner_while_locked_and_completion_inherits_unlock(homes, tmp_path):
    from hermes_cli.update_vault import child_vault

    source = Path(__file__).resolve().parents[2]
    home, sibling = homes
    selected = tmp_path / "selected.py"
    selected.write_text('''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hermes_cli import source_completion
from hermes_security import io
def complete(root, **kwargs):
    for home in sys.argv[2:]:
        home = Path(home)
        assert io.read_text(home / "config.yaml", purpose="config") == f"model: {home.name}-canary\\n"
    return True
source_completion.complete_source_checkout = complete
raise SystemExit(source_completion.main(["--source", sys.argv[1], "--prepared", "--finish-update"]))
''', encoding="utf-8")
    preparation = tmp_path / "prepare.py"
    preparation.write_text('''
import json, os, subprocess, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
class NoCrypto:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "cryptography" or fullname.startswith("cryptography."):
            raise AssertionError("preparation loaded application crypto")
sys.meta_path.insert(0, NoCrypto())
from pm.state_io import read_text
from pm.runtime import runtime_environment
from hermes_cli import update_receipt, update_completion
from hermes_cli.update_vault import child_vault
from hermes_cli.runtime_state import runtime_lock
with runtime_lock(Path(sys.argv[1])):
    for home in (sys.argv[3], sys.argv[4], sys.argv[3]):
        home = Path(home)
        assert read_text(home / "config.yaml", purpose="config") == f"model: {home.name}-canary\\n"
    update_receipt.begin_update_receipt()
    request = {"home": sys.argv[3], "receipt": {"update_id": update_receipt.current_correlation_id()}}
    from pm import receipt
    receipt.accept_worker_receipt({"update_id": request["receipt"]["update_id"], "kind": "sync", "outcome": "failed"},
                                  request["receipt"]["update_id"])
    assert receipt.latest()["kind"] == "sync"
    saved = update_receipt.finalize_pending_update_receipt(1, "a preparation failure must leave a sealed receipt")
    assert saved and saved.read_bytes().startswith(b"HRMVAULT\\0")
    assert update_completion._read_terminal_receipt(request)["outcome"] == "failed"
assert "HERMES_UPDATE_VAULT_CHANNEL" not in runtime_environment()
env = dict(os.environ)
with child_vault(env) as kwargs:
    result = subprocess.run([sys.executable, "-I", sys.argv[2], sys.argv[1], sys.argv[3], sys.argv[4]],
                            env=env, **kwargs)
assert result.returncode == 0
''', encoding="utf-8")
    env = dict(os.environ)
    # The original bug tried to activate the graph inside the callback while
    # PM already held this lock, then could not import cryptography under -S.
    with child_vault(env) as kwargs:
        result = subprocess.run([sys.executable, "-I", "-S", str(preparation), str(source),
                                 str(selected), str(home), str(sibling)],
                                env=env, capture_output=True, text=True, timeout=45, **kwargs)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "dependency lock still held" not in result.stderr
    assert "Unlock master password" not in result.stdout + result.stderr
    for path in home.glob("logs/update_receipts/*.json"):
        assert path.read_bytes().startswith(b"HRMVAULT\0")
        assert json.loads(io.read_text(path, purpose="state", encoding="utf-8"))["outcome"] == "failed"


@pytest.mark.platforms("any")
def test_preparation_cannot_use_vault_channel_for_other_state(homes, tmp_path):
    from hermes_cli.update_vault import child_vault

    source = Path(__file__).resolve().parents[2]
    home, _ = homes
    io.write_text(home / ".env", "TOKEN=secret-canary\n", purpose="env", encoding="utf-8")
    before = (home / ".env").read_bytes()
    program = '''
import base64, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from pm.state_io import owner_operation
path = Path(sys.argv[2])
try:
    owner_operation("decrypt", str(path), base64.b64encode(path.read_bytes()).decode(), "env")
except RuntimeError as exc:
    assert "not an authorized update envelope operation" in str(exc)
else:
    raise AssertionError("preparation decrypted non-PM state")
'''
    script = tmp_path / "restricted.py"
    script.write_text(program, encoding="utf-8")
    env = dict(os.environ)
    with child_vault(env) as kwargs:
        result = subprocess.run([sys.executable, "-I", "-S", str(script), str(source), str(home / ".env")],
                                env=env, capture_output=True, text=True, timeout=30, **kwargs)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / ".env").read_bytes() == before
