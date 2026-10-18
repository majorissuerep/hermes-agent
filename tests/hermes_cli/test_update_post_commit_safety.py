"""Native committed-update receipts retain configuration and retryable tail truth.

Each cell owns a disposable home and real Git checkout. The selected SQLite probe
is a controlled interpreter boundary; no service runtime or remote migration exists.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _child_env(**values: str) -> dict[str, str]:
    """A fresh env for a child of THIS interpreter: same dependency path, no hermes state."""
    env = {key: value for key, value in os.environ.items() if not key.startswith(("HERMES_", "PYTEST_"))}
    # The test runner may provide dependencies through sys.path rather than site-packages.
    env["PYTHONPATH"] = os.pathsep.join(path for path in sys.path if path)
    env.update(values)
    return env

_CHILD = textwrap.dedent('''
    import copy, json, os, subprocess, sys
    from pathlib import Path
    root_checkout, cell, mode = sys.argv[1:4]
    sys.path.insert(0, root_checkout)
    cell = Path(cell)
    source = cell / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text('[project]\\nname="completion-probe"\\nversion="0.0.0"\\n')
    git = ["git", "-c", "user.email=probe@example.invalid", "-c", "user.name=Probe", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run([*git, "-C", str(source), "commit", "-qm", "fixture"], check=True)
    if mode.startswith("sqlite"):
        # The selected interpreter is the shim: its SQLite probe is the runtime-safety fact.
        sys.executable = os.environ["PROBE_SHIM"]
    import hermes_yaml as yaml
    from hermes_cli.config import DEFAULT_CONFIG
    home = Path(os.environ["HERMES_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(yaml.safe_dump({
        "_config_version": DEFAULT_CONFIG["_config_version"] - 1,
        "telemetry": {"shared_metrics": {"enabled": False}},
        "updates": {"refresh_cua_driver": False}}))
    from tests.e2e.vault_home import seal_fixture_home
    seal_fixture_home(home)
    from hermes_security.vault import get_vault
    from hermes_cli import update_cmd_maint as maint
    windows_resume = None
    if mode == "profile-sync":
        def profile_sync():
            raise SystemExit("profile skill sync exited 3")
        maint._sync_profiles_after_update = profile_sync
    from hermes_cli import update_receipt
    from hermes_cli.venv_sync import arm_completion, completion_pending_path
    arm_completion(source)
    update_receipt.begin_update_receipt()
    receipt = copy.deepcopy(update_receipt._current.get().data)
    # The completion process never inherits the parent's open receipt context.
    update_receipt._current.reset(update_receipt._current.get().current_token)
    request = {"schema": 1, "source": str(source), "home": str(home), "branch": "main", "desktop": False,
               "assume_yes": True, "gateway_mode": mode.startswith("ipc-"),
               "no_gateway_restart": mode == "config-ro",
               "pre_update_version": None, "snapshot_id": None, "sibling_snapshots": {}, "plan": None,
               "receipt": receipt, "windows_resume": windows_resume}
    if mode == "config-ro":
        home.chmod(0o555)  # a late filesystem failure: the profile home refuses the config write
    if mode.startswith("receipts-blocked"):
        # The receipt store fails after the commit point: its directory is now an ordinary file.
        import shutil
        shutil.rmtree(update_receipt._receipt_dir())
        update_receipt._receipt_dir().write_text("not a directory")
    from hermes_cli.update_completion import _finish, _settle_after_commit
    try:
        if mode == "receipts-blocked-settle":
            code = _settle_after_commit(request, cell / "result.json", "dependencies", "dependency sync refused")
        else:
            code = _finish(request, cell / "result.json")
    finally:
        home.chmod(0o755)
    latest = update_receipt.read_latest_receipt() or {}
    result = json.loads((cell / "result.json").read_text()) if (cell / "result.json").exists() else None
    (cell / "summary.json").write_text(json.dumps({
        "code": code, "outcome": latest.get("outcome"), "update_id": receipt["update_id"], "result": result,
        "followups": [row["step"] for row in latest.get("followups", [])],
        "stages": {mark["name"]: mark["outcome"] for mark in latest.get("stages", [])},
        "config_version": yaml.safe_load(get_vault(home).read_bytes(home / "config.yaml", purpose="config").decode())["_config_version"],
        "latest_config_version": DEFAULT_CONFIG["_config_version"],
        "pending": completion_pending_path(source).is_file(),
        "stamp": (source / "install-stamp.json").is_file(),
        }))
''')


def _run_completion(tmp_path: Path, mode: str, *, sqlite: str | None = None) -> dict:
    cell = tmp_path / "cell"
    root_home = cell / "home" / ".hermes"
    # A named profile: receipts stay in the (writable) root home while the profile home refuses.
    profile = root_home / "profiles" / "probe"
    for name in ("logs/update_receipts", "skills", "cache", "profiles/probe"):
        (root_home / name).mkdir(parents=True, exist_ok=True)
    env = _child_env(HOME=str(cell / "home"), HERMES_HOME=str(profile), HERMES_DISABLE_LAZY_INSTALLS="1",
                     HERMES_RUNTIME_DIR=str(tmp_path / "store"), PYTHONDONTWRITEBYTECODE="1",
                     XDG_CONFIG_HOME=str(cell / "home/.config"), XDG_CACHE_HOME=str(cell / "home/.cache"),
                     XDG_STATE_HOME=str(cell / "home/.local/state"))
    if sqlite is not None:
        payload = json.dumps({"base_prefix": sys.base_prefix, "executable": sys.executable,
                              "python_version": list(sys.version_info[:3]),
                              "sqlite_version": [int(p) for p in sqlite.split(".")],
                              "sqlite_version_string": sqlite, "sqlite_source_id": "fixture"})
        shim = tmp_path / "python-shim"
        script = (f"#!/bin/sh\ncase \"$3\" in *sqlite_source_id*) printf '%s\\n' '{payload}'; exit 0;; esac\n"
                  f"exec '{sys.executable}' \"$@\"\n")
        shim.write_text(script, encoding="utf-8")
        shim.chmod(0o755)
        env["PROBE_SHIM"] = str(shim)
    child = tmp_path / "child.py"
    child.write_text(_CHILD, encoding="utf-8")
    done = subprocess.run([sys.executable, "-B", str(child), str(ROOT), str(cell), mode],
                          env=env, cwd=tmp_path, capture_output=True, text=True, timeout=240, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    summary = json.loads((cell / "summary.json").read_text(encoding="utf-8-sig"))
    summary["output"] = done.stdout + done.stderr
    return summary


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("sqlite, safe", [("3.46.1", False), ("3.53.1", True)])
def test_selected_runtime_safety_is_reported_without_failing_committed_update(tmp_path, sqlite, safe):
    summary = _run_completion(tmp_path, f"sqlite-{sqlite}", sqlite=sqlite)
    # Committed code: success and exit 0 either way.
    assert summary["code"] == 0, summary["output"]
    assert summary["outcome"] == "success"
    if safe:
        assert summary["followups"] == []
    else:
        # The unsafe runtime is a visible, owed step — and it must still veto the migration.
        assert f"SQLite ({sqlite})" in summary["output"]
        assert summary["followups"] == ["sqlite_runtime"]


@pytest.mark.platforms("posix")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores directory modes")
def test_config_format_write_failure_is_owed_not_reported_complete(tmp_path):
    summary = _run_completion(tmp_path, "config-ro")
    assert summary["code"] == 0, summary["output"]
    assert summary["config_version"] == summary["latest_config_version"] - 1
    # The receipt names the debt, the tail stays pending and nothing stamps the tree as done.
    assert summary["followups"] == ["config_migration"]
    assert summary["pending"] is True
    assert summary["stamp"] is False
    # C3: the receipt names what actually failed. The build succeeded; only the config is owed.
    assert summary["stages"].get("build") == "success", summary["stages"]


@pytest.mark.platforms("posix")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores directory modes")
def test_profile_sync_followup_keeps_the_tail_owed(tmp_path):
    summary = _run_completion(tmp_path, "profile-sync")
    assert summary["code"] == 0, summary["output"]
    assert summary["outcome"] == "success"
    assert summary["followups"] == ["profile_sync"]
    # A profile sync that did not finish is tail work: retried by the next launch, never stamped done.
    assert summary["pending"] is True
    assert summary["stamp"] is False


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("path", ["finish", "settle"])
def test_receipt_store_failure_after_commit_keeps_exit_zero_and_a_correlated_receipt(tmp_path, path):
    """The receipt store is written after the commit point: losing it is loud, never exit 1."""
    summary = _run_completion(tmp_path, f"receipts-blocked-{path}")
    assert "Update receipt not written" in summary["output"]
    assert summary["code"] == 0, summary["output"]
    result = summary["result"]
    assert result["exit_code"] == 0
    # run_completion's correlation gate: this run's id, terminal, and its outcome agrees with exit 0.
    receipt = result["receipt"]
    assert receipt is not None, summary["output"]
    assert receipt["update_id"] == summary["update_id"]
    assert receipt["finished_at"]
    assert receipt["outcome"] == "success"
    # Retry obligations are unchanged: owed dependencies stay armed, a finished tail is cleared.
    if path == "settle":
        assert [row["step"] for row in receipt["followups"]] == ["dependencies"]
    assert summary["pending"] is (path == "settle")




# The NEW tree's completion child after a committed update whose Windows resume the service
# manager refused (what _finish answers): the run is a success with a windows_resume follow-up,
# and the token it hands back still owes the restart.
