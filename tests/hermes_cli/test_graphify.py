"""The bundled project graph CLI works from a locked agent terminal child."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_project_graph_cli_uses_bundled_runtime_without_unlocking_hermes(tmp_path, monkeypatch):
    from hermes_security import io, vault

    home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("GRAPHIFY_OUT", raising=False)
    vault.init_vault(home, "graph-cli-test-password")
    vault.unlock(home, "graph-cli-test-password")
    io.write_text(home / "config.yaml", "model: private-test-model\n", purpose="config")  # windows-footgun: ok — sealed-state helper, UTF-8 by default
    encrypted_config = (home / "config.yaml").read_bytes()
    vault.clear_vault_cache()
    project = tmp_path / "codebase"
    project.mkdir()
    (project / "app.py").write_text("def greet():\n    return 'hello'\n", encoding="utf-8")
    env = dict(os.environ)
    for key in ("HERMES_ALLOW_NO_VAULT", "HERMES_VAULT_PRIVATE_KEY", "HERMES_VAULT_KEY_FD"):
        env.pop(key, None)

    def cli(*args, expected=0):
        result = subprocess.run(
            [sys.executable, "-m", "hermes_cli.main", *args],
            env=env, cwd=Path(__file__).resolve().parents[2], input="",
            capture_output=True, text=True, timeout=60)
        assert result.returncode == expected, result.stdout + result.stderr
        return result

    status = json.loads(cli("graphify", "scan", str(project)).stdout)
    assert not status["graph_exists"]
    built = json.loads(cli("graphify", "build", str(project)).stdout)
    assert Path(built["graph"]).is_relative_to(project)
    assert "greet" in cli("graphify", "query", str(project), "greet").stdout
    # Project-only exemption cannot be combined with a state-touching agent invocation.
    cli("--oneshot", "do a task", "graphify", "scan", str(project), expected=2)
    assert (home / "config.yaml").read_bytes() == encrypted_config
