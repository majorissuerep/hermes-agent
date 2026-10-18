"""Frozen historical updater entry; completion now builds and maintains only the local TUI."""
from __future__ import annotations

import json
from pathlib import Path
import sys


def finish_update(*, root, assume_yes, gateway_mode, pre_update_snapshot_id,
                  had_desktop_app_before_update, pre_update_version,
                  plan, windows_resume, followups=None) -> None:
    """Accept old serialized fields without restarting the retired desktop or services."""
    from hermes_cli.source_completion import complete_source_checkout
    from hermes_cli.update_receipt import TAIL_FOLLOWUPS, record_build_stage, record_followup
    from hermes_cli.venv_sync import clear_completion

    owed = followups if followups is not None else []
    try:
        complete_source_checkout(
            Path(root), assume_yes=assume_yes,
            pre_update_snapshot_id=pre_update_snapshot_id,
            pre_update_version=pre_update_version, followups=owed)
    except (Exception, SystemExit) as exc:  # health: allow BLE001 -- committed source: retain retryable completion debt
        reason = str(exc) or type(exc).__name__
        record_followup("maintenance", reason)
        owed.append(("maintenance", reason))
    record_build_stage(owed)
    if not any(step in TAIL_FOLLOWUPS for step, _ in owed):
        clear_completion(Path(root))


def main(context: Path, result: Path) -> int:
    request = json.loads(context.read_text(encoding="utf-8-sig"))
    root = Path(request["root"])
    sys.path.insert(0, str(root))
    from hermes_cli import update_receipt

    restarting = request.get("restart_update", False)
    cli_started = False
    begun = False
    code = 1
    try:
        if not restarting:
            update_receipt.begin_update_receipt(previous=request.get("receipt"), correlation_id=request["update_id"])
            begun = update_receipt._current.get() is not None
            if not begun:
                raise RuntimeError("cannot create takeover receipt")
            from pm import receipt
            receipt.accept_worker_receipt(request.get("pm_receipt"), request["update_id"])
            update_receipt.record_step("historical_takeover", True, "continuing in the selected interpreter")
        sys.argv = list(request["argv"]) if restarting else [str(root / "hermes"), "update"]
        import hermes_bootstrap
        from hermes_cli.update_vault import adopt_keys
        adopt_keys()
        from hermes_cli import main as cli
        from hermes_cli.update_lock import UpdateLock, describe_holder
        from hermes_cli.venv_sync import arm_completion

        cli.PROJECT_ROOT = root
        if restarting:
            cli_started = True
            cli.main()
        else:
            with UpdateLock(install_root=root) as lock:
                if not lock.acquired and lock.holder is not None:
                    print(describe_holder(lock.holder), file=sys.stderr)
                    return 2
                arm_completion(root)
                finish_update(
                    root=root, assume_yes=request["assume_yes"], gateway_mode=False,
                    pre_update_snapshot_id=request.get("pre_update_snapshot_id"),
                    had_desktop_app_before_update=False,
                    pre_update_version=request.get("pre_update_version"), plan=None,
                    windows_resume=None)
        code = 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    except Exception as exc:  # health: allow BLE001 -- historical request must receive its failure acknowledgement
        update_receipt.record_step("historical_completion", False, str(exc))
        print(f"Update completion failed: {exc}", file=sys.stderr, flush=True)
    finally:
        if restarting and not cli_started:
            update_receipt.begin_update_receipt(previous=request.get("receipt"), correlation_id=request["update_id"])
            begun = update_receipt._current.get() is not None
        handled = cli_started or (begun and update_receipt._current.get() is None)
        written = update_receipt.finalize_pending_update_receipt(code, "historical takeover completion")
        result.write_text(json.dumps({
            "resume_handled": True, "receipt_handled": handled or written is not None,
        }), encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))
