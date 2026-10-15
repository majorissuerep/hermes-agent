"""The isolated oracle must preserve real encrypted state, not merely return green."""
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
    vault.unlock(home, password)
    from hermes_cli.config import save_config, save_env_value
    save_config({"model": {"provider": "custom", "base_url": "https://old.example.invalid/v1",
                           "api_key": "synthetic-old-route-key"}, "agent": {"max_turns": 37}}, merge_existing=True)
    from hermes_security import io
    io.write_text(home / "memories/legacy.md", "synthetic-legacy-nonce", purpose="memory")
    save_env_value("E2E_LEGACY_SECRET", "synthetic-legacy-nonce")
    vault.clear_vault_cache()
    root = Path(__file__).resolve().parents[3]
    script = tmp_path / "state_probe.py"
    script.write_text((root / "scripts/e2e/state_probe.py").read_text())
    boot = ("import runpy, sys; sys.path.insert(0, " + repr(str(root)) + "); "
            "runpy.run_path(" + repr(str(script)) + ", run_name='__main__')")

    def probe(action, **extra):
        result = subprocess.run(
            [sys.executable, "-c", boot], check=False, cwd=root, text=True, capture_output=True,
            input=json.dumps({"action": action, "password": password, **extra}), timeout=60,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout.strip().splitlines()[-1])

    legacy = probe("legacy-proof", nonce="synthetic-legacy-nonce", max_turns=37)
    seeded = probe("seed", workspace=str(tmp_path), credential={
        "provider": "litellm", "model": "acceptance-model", "base_url": "http://localhost:9/v1",
        "api_key": "synthetic-unused-gateway-key", "api_mode": "chat_completions",
    })
    assert probe("manifest") == seeded
    assert probe("legacy-proof", nonce="synthetic-legacy-nonce", max_turns=17)["memory"] == legacy["memory"]
    snapshot = probe("snapshot")["snapshot"]
    assert probe("restore", snapshot=snapshot) == seeded
    assert probe("manifest") == seeded
