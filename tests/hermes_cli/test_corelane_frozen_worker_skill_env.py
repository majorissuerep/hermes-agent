"""A frozen-policy worker never opens the profile's ``.env`` -- including the skill readiness probe.

``hermes_cli.config.load_env`` already returns ``{}`` under ``worker_config_snapshot()``; the
``skill_view`` readiness snapshot must route through it instead of tokenizing the file itself.
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _run(tmp_path, mode, monkeypatch):
    home = tmp_path / mode
    (home / "skills" / "probe").mkdir(parents=True)
    (home / ".env").write_text("CORELANE_PROBE_KEY=from-profile-dotenv\n", encoding="utf-8")
    (home / "config.yaml").write_text("agent: {}\n", encoding="utf-8")
    (home / "skills" / "probe" / "SKILL.md").write_text(
        "---\nname: probe\ndescription: probe\nrequired_environment_variables:\n"
        "  - name: CORELANE_PROBE_KEY\n    prompt: key\n---\nbody\n", encoding="utf-8")
    from tests.e2e.vault_home import seal_fixture_home, run_unlocked
    monkeypatch.setenv("HERMES_HOME", str(home))
    seal_fixture_home(home)
    script = tmp_path / "probe.py"
    script.write_text(f"import sys; sys.path.insert(0, {str(ROOT)!r})\n" + textwrap.dedent("""
        import json, os, sys
        if os.environ["PROBE_MODE"] == "config":
            from agent.safe_worker_policy import _bind_safe_worker_policy
            _bind_safe_worker_policy(safe_mode=False, ignore_user_config=True, config={})
        from hermes_security.handoff import adopt_inherited_key
        assert adopt_inherited_key()
        home = os.environ["HERMES_HOME"]
        opened = []
        sys.addaudithook(lambda e, a: opened.append(str(a[0]))
                         if e == "open" and str(a[0]).startswith(home) and str(a[0]).endswith(".env") else None)
        from tools.skills_tool import skill_view
        result = json.loads(skill_view("probe"))
        print(json.dumps({"opened": opened, "missing": result.get("missing_required_environment_variables")}))
    """), encoding="utf-8")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("HERMES_VAULT_", "PYTEST_")) and key not in {"PYTHONPATH", "PYTHONHOME"}}
    env.update(HOME=str(home), USERPROFILE=str(home), HERMES_HOME=str(home), PROBE_MODE=mode, HERMES_DISABLE_LAZY_INSTALLS="1")
    proc = run_unlocked([sys.executable, str(script)], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=90, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("mode", ["config", "ordinary"])
def test_skill_readiness_never_reads_profile_dotenv_in_frozen_worker(tmp_path, mode, monkeypatch):
    receipt = _run(tmp_path, mode, monkeypatch)
    if mode == "ordinary":
        # Positive control: the ordinary loader sees the profile credential.
        assert receipt["opened"] and receipt["missing"] == [], receipt
    else:
        assert receipt["opened"] == [], receipt
        assert receipt["missing"] == ["CORELANE_PROBE_KEY"], receipt
