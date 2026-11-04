"""Bounded real-process management cohort; original assertions and fixtures retained."""

from __future__ import annotations
import os
from pathlib import Path
import pytest
from tests.hermes_cli.test_managed_agent_supervision import (_run_owner, _vendor)

pytest_plugins = ["tests.hermes_cli.test_managed_agent_supervision"]
pytestmark = pytest.mark.live_system_guard_bypass


@pytest.mark.platforms("linux")
def test_vendor_temp_writes_use_a_removed_protected_task_scratch(world, tmp_path, ram_runtime):
    report = tmp_path / "report"
    _vendor(tmp_path / "executors", f"""set -e
case "$TMPDIR" in "{ram_runtime}"/hermes-managed-codex-*) ;; *) exit 41 ;; esac
[ "$TMP" = "$TMPDIR" ] && [ "$TEMP" = "$TMPDIR" ] && [ "$(stat -c %a "$TMPDIR")" = 700 ]
printf synthetic-canary > "$TMPDIR/canary"
printf '%s' "$TMPDIR" > '{report}'
echo protected-scratch-ok
""")
    env = dict(world.env, PATH=str(tmp_path / "executors") + os.pathsep + world.env["PATH"],
               XDG_RUNTIME_DIR=str(ram_runtime))
    returncode, stdout, stderr = _run_owner(world, env)
    assert returncode == 0, (stdout, stderr)
    assert stdout.strip() == "protected-scratch-ok"
    assert not Path(report.read_text()).exists()


@pytest.mark.platforms("linux")
def test_sandboxed_vendor_reaches_the_owning_management_route(world, tmp_path, ram_runtime):
    from hermes_security.sandbox.spawn import backend_status

    status = backend_status()
    if not status["available"]:
        pytest.skip(f"native sandbox unavailable: {status['detail']}")
    result, output = world.call({"op": "config.set", "path": ["sandbox", "enabled"], "value": True,
                                 "revision": world.get()["revision"]})
    assert result.returncode == 0, output
    _vendor(tmp_path / "executors", """set -e
answer=$(printf '%s' '{"op": "config.get", "path": ["agent", "max_turns"]}' | hermes manage apply)
case "$answer" in *'"value": 19'*) echo sandboxed-route-ok ;; *) echo "$answer" >&2; exit 42 ;; esac
""")
    env = dict(world.env, PATH=str(tmp_path / "executors") + os.pathsep + world.env["PATH"],
               XDG_RUNTIME_DIR=str(ram_runtime))
    returncode, stdout, stderr = _run_owner(world, env)
    assert returncode == 0, (stdout, stderr)
    assert stdout.strip() == "sandboxed-route-ok"
