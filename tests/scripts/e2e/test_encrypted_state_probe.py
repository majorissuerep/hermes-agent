"""The VM oracle must preserve real encrypted state, not merely return green."""
import json
from pathlib import Path
import subprocess
import sys

from hermes_security import vault


def test_real_state_oracle_survives_snapshot_mutation_and_restore(tmp_path, monkeypatch):
    home = tmp_path / "state"
    monkeypatch.setenv("HERMES_HOME", str(home))
    password = "synthetic-rehearsal-password"
    vault.init_vault(home, password)
    vault.clear_vault_cache()
    root = Path(__file__).resolve().parents[3]
    script = (root / "scripts/e2e/state_probe.py").read_text()

    def probe(action, **extra):
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=root, text=True, capture_output=True,
            input=json.dumps({"action": action, "password": password, **extra}), timeout=60,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout.strip().splitlines()[-1])

    seeded = probe("seed", workspace=str(tmp_path), credential={
        "provider": "litellm", "model": "acceptance-model", "base_url": "http://localhost:9/v1",
        "api_key": "synthetic-unused-gateway-key", "api_mode": "chat_completions",
    })
    assert probe("manifest") == seeded
    snapshot = probe("snapshot")["snapshot"]
    assert probe("restore", snapshot=snapshot) == seeded
    assert probe("manifest") == seeded
