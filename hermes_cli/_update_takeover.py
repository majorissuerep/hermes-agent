"""Absolute-path bootstrap for historical updater takeover; no app imports yet."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def prepare(request: dict) -> tuple[Path, dict[str, str]]:
    """Provision the new graph before entering its application interpreter."""
    root = Path(request["root"])
    sys.path.insert(0, str(root))
    # The old shim's UI has been frozen since the pull; from here the new
    # tree can publish stages (and pop the panel the old shim couldn't).
    from hermes_cli.update_stage import ensure_panel, publish_stage

    ensure_panel(root)
    # An older updater hands off here instead of update_completion._prepare.
    from hermes_cli.gitlock import convert_treeless_checkout_first
    convert_treeless_checkout_first(root)
    publish_stage("Updating Python dependencies (PM)")
    from pm import receipt
    from pm.client import ensure_tools_for_sync, sync_venv, venv_is_current
    from pm.environments import activation_environment, install_state_dir, runtime_facts_path
    from hermes_cli._launchers import resolve_store_python
    from hermes_cli.venv_sync import (
        arm_completion, collect_superseded_generations, publish_launchers, refuse_foreign_owned_venv)

    # The historical updater already moved the tree: owe the tail and the fleet restart before the
    # first slow step, exactly like a current updater's commit point, so a kill from here on leaves
    # both for the next launch / `hermes update` instead of nothing.
    refuse_foreign_owned_venv(root)
    arm_completion(root)
    correlation = request["update_id"]
    with receipt.worker_context(correlation):
        # A pre-PM installation has no required-tool facts. A current Python
        # generation alone does not prove its Node/Git/tool closure is ready.
        ensure_tools_for_sync()
        from pm.extras import legacy_selection

        extras = legacy_selection(root) if not runtime_facts_path(root).is_file() else None
        repair_marker = install_state_dir(root) / ".repair-incomplete"
        # Repair preserves the old stamp. Changed source inputs instead need
        # an ordinary sync, which builds and validates a fresh generation too.
        repair = repair_marker.is_file() and venv_is_current(project_root=root)
        # An update never fails because of a plugin: misfits are disabled and reported.
        sync_venv(None if repair else extras, explicit=True, project_root=root, repair=repair,
                  evict_incompatible_plugins=not repair)
        collect_superseded_generations(root)
        request["pm_receipt"] = receipt.last_for_update(correlation)
    repair_marker.unlink(missing_ok=True)
    for name in (".update-incomplete", ".lazy-refresh-incomplete"):
        (root / name).unlink(missing_ok=True)
    python = resolve_store_python(root)
    if python is None:
        raise RuntimeError("updated installation has no managed interpreter")
    return python, activation_environment(root)






def _record_failure(request: dict, result: Path, code: int, detail: str) -> None:
    from hermes_cli import update_receipt
    from hermes_constants import get_hermes_home

    update_receipt.record_step("historical_takeover", False, detail)
    saved = update_receipt.finalize_pending_update_receipt(code, detail)
    if request.get("gateway_mode"):
        (get_hermes_home() / ".update_exit_code").write_text(f"{code}\n", encoding="utf-8")
    result.write_text(json.dumps({"receipt_handled": saved is not None, "resume_handled": False}), encoding="utf-8")


def main() -> int:
    context, result = map(Path, sys.argv[1:3])
    request = json.loads(context.read_text(encoding="utf-8-sig"))
    sys.path.insert(0, request["root"])
    from hermes_cli.update_vault import child_vault
    if "stopped_serves" in request:
        result.write_text(json.dumps({"receipt_handled": False, "resume_handled": True}), encoding="utf-8")
        return 0
    from hermes_cli import update_receipt
    from hermes_cli.update_lock import UpdateLock, describe_holder

    # The takeover syncs dependencies and its update_finish child builds the checkout: hold
    # (or join, inherited from the old updater) the checkout lock, not just the marker (R2).
    lock = UpdateLock(install_root=Path(request["root"]))
    if not lock.acquire():
        print(describe_holder(lock.holder), file=sys.stderr)
        return 2
    old_receipt = request.get("receipt") or {}
    update_receipt.begin_update_receipt(previous=old_receipt, correlation_id=old_receipt.get("update_id"))
    request["update_id"] = update_receipt.current_correlation_id()
    try:
        try:
            python, env = prepare(request)
            request["receipt"] = update_receipt._current.get().data
            context.write_text(json.dumps(request), encoding="utf-8")
        except Exception as exc:  # health: allow BLE001 -- pre-commit boundary: any preparation failure is the takeover's recorded failure
            print(f"Update preparation failed: {exc}", file=sys.stderr, flush=True)
            _record_failure(request, result, 1, f"historical takeover preparation failed: {exc}")
            return 1
        try:
            return _run_finish_child(request, context, result, python, env)
        except Exception as exc:  # health: allow BLE001 -- post-commit boundary: the tree moved and the tail is armed
            _record_owed_finish(request, result, f"the update finish child could not run ({exc})")
            return 0
    finally:
        lock.release()


def _run_finish_child(request: dict, context: Path, result: Path, python: Path, env: dict) -> int:
    # This file is new too. Direct execution bypasses normal launch-time
    # update liveness checks while the waiting parent still holds its lock.
    command = [str(python), "-I", "-B", "-X", "utf8", str(Path(request["root"]) / "hermes_cli/update_finish.py"),
               str(context), str(result)]
    from hermes_cli.update_custody import popen_post_commit

    # update_finish builds the checkout: POSIX lock fd; Windows bound to the job (or leased).
    from hermes_cli.update_vault import child_vault

    with child_vault(env) as vault_kwargs, popen_post_commit(
            command, label="update finish child", cwd=request["root"], env=env, **vault_kwargs) as child:
        try:
            code = child.wait()
        except BaseException:
            child.kill()
            raise
    if code != 0 and not result.is_file():
        _record_failure(request, result, code, f"completion child exited {code} without acknowledgement")
    return code


def _record_owed_finish(request: dict, result: Path, detail: str) -> None:
    """The historical updater already moved the tree and ``prepare`` armed the completion tail, so a
    finish child that could not be started or resumed is an owed follow-up, never a failed update:
    the next launch runs the tail. Receipt finalized with exit 0. Never raises."""
    from contextlib import suppress

    print(f"  ⚠ The update is installed, but its finishing steps did not run: {detail}. "
          "The next `hermes` launch finishes them.", file=sys.stderr, flush=True)
    saved = None
    # Receipt/exit-code writes are best effort: a committed update exits 0 whatever they hit.
    with suppress(Exception):  # health: allow BLE001 -- post-commit boundary: a receipt error never fails the update
        from hermes_cli import update_receipt

        update_receipt.record_step("historical_takeover", False, f"owed: {detail}")
        saved = update_receipt.finalize_pending_update_receipt(0, f"committed; finishing steps owed: {detail}")
    if request.get("gateway_mode"):
        with suppress(Exception):  # health: allow BLE001 -- post-commit boundary: see above
            from hermes_constants import get_hermes_home

            (get_hermes_home() / ".update_exit_code").write_text("0\n", encoding="utf-8")
    with suppress(OSError):  # no result file: the old updater keeps its own receipt and resumes
        result.write_text(json.dumps({"receipt_handled": saved is not None, "resume_handled": False}),
                          encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
