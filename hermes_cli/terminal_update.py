"""Source update orchestration for a terminal-owned runtime.

Git movement, snapshots, stash restoration and fresh-interpreter PM completion retain
one transaction; no process supervisor or background service participates.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def update_source(args) -> None:
    """Apply a source update through the existing transaction and completion seams."""
    from hermes_cli import main, update_cmd as update
    from hermes_cli import update_cmd_check as check
    from hermes_cli import update_receipt
    from hermes_cli.gitlock import release_dead_index_lock
    from hermes_cli._early_recovery import git_operation_in_progress

    root = main.PROJECT_ROOT
    operation = git_operation_in_progress(root)
    if operation:
        from hermes_cli.update_cmd_common import _record_stop
        _record_stop("git_in_progress", without_receipt="failed")
        raise RuntimeError(f"Finish the Git {operation} in {root} before updating.")
    release_dead_index_lock(root)
    check.clear_git_debris(root)
    update._commit.begin_update_attempt()
    options = update._resolve_update_options(args, False)
    update_receipt.begin_update_receipt()
    snapshot = update._run_pre_update_backup(args)
    update._record_pre_update_backup_outcome(args, snapshot)
    update._record_snapshot_stage(args, snapshot)
    request = update._source_completion_request(options, None, snapshot, None, False, False)
    release_sha = repository = None
    target_selected = False
    try:
        use_zip, git, is_fork = update._prepare_git_command()
        branch = main._resolve_update_branch(args)
        request['branch'] = branch
        target_ref, release_sha, target_is_head, repository = check.select_apply_target(
            args, branch, request, git_cmd=None if use_zip else git, stop=lambda: None)
        target_selected = True
        if not use_zip:
            _apply_git(args, options, request, git, is_fork, target_ref, release_sha, target_is_head)
            return
    except subprocess.CalledProcessError as error:
        # Only the established untouched-Windows-Git predicate may authorize an overlay.
        # Ownership refusals and post-move/dependency failures retain their original boundary.
        if not update._should_zip_fallback_on_update_error(error):
            raise
        if not target_selected:
            branch = main._resolve_update_branch(args)
            request['branch'] = branch
            _, release_sha, _, repository = check.select_apply_target(
                args, branch, request, git_cmd=None, stop=lambda: None)
        print("→ Git file I/O failed; falling back to the selected source ZIP...")
    update._update_via_zip(args, had_desktop_app_before_update=False,
        target_sha=release_sha, completion_request=request,
        **({'target_repository': repository} if repository else {}))


def _apply_git(args, options, request, git, is_fork, target_ref, release_sha, target_is_head):
    """Git-only stage; its failures retain the preselected identity for bounded ZIP recovery."""
    from hermes_cli import main, update_cmd as update, update_cmd_check as check

    root, branch = main.PROJECT_ROOT, request['branch']
    update._heal_stale_shallow_checkout(root, branch)
    fetch = None if release_sha and target_is_head else (
        ['fetch', '--no-tags', 'origin', target_ref] if release_sha else
        ['fetch', 'origin', check.tracking_refspec('origin', branch)])
    if fetch:
        from hermes_cli.gitlock import fetch_with_partial_clone_recovery
        result = fetch_with_partial_clone_recovery(
            lambda command, argv: update._git_run(command, argv, network=True), git, fetch, root)
        if result.returncode:
            raise subprocess.CalledProcessError(result.returncode, [*git, *fetch],
                                                output=result.stdout, stderr=result.stderr)
    update._refuse_before_commit_point(git, target_ref, None)
    current_branch = update._current_branch_name(git, check=True)
    update._commit.record_run_start(git, root)
    plan = update._prepare_checkout_for_update(git, branch, current_branch, is_fork=is_fork,
        assume_yes=options.assume_yes, gateway_mode=False, gw_input_fn=None,
        switch_branch=options.switch_branch, target_ref=target_ref, _windows_gateway_resume=None)
    if plan.commit_count == 0:
        update._finish_already_up_to_date(git, branch, current_branch, plan,
            gw_input_fn=None, completion_request=request, _windows_gateway_resume=None)
        return
    baseline = update._pull_updates(git, branch, plan.auto_stash_ref,
        prompt_for_restore=plan.prompt_for_restore, gw_input_fn=None,
        discard_local_changes=options.discard_local_changes, keep_stash=options.keep_stash,
        target_ref=target_ref, pre_sync_sha=plan.pre_sync_sha, rollback_branch=plan.rollback_branch,
        sync_upstream=is_fork and branch == 'main' and not release_sha,
        assume_yes=options.assume_yes, in_place_update=plan.in_place_update,
        _windows_gateway_resume=None, expected_sha=request.get('expected_sha'))
    update._apply_pulled_update(git, branch, baseline, plan,
                               _windows_gateway_resume=None, completion_request=request)


def print_plan(root: Path) -> None:
    """Describe terminal update scope without probing services or opening sockets."""
    from hermes_cli.update_contract import evaluate_update_admission
    refusal = evaluate_update_admission(root)
    if refusal:
        print(refusal.message)
        return
    print(f"Source checkout: {root}")
    print("Snapshot state; preserve local changes; select source commit; prepare PM runtime and Ink TUI.")
    print("Running terminals are not restarted; exit and resume them to use the updated runtime.")


def format_update_failure(error: Exception) -> str:
    """Keep bounded captured command diagnostics, with credentials removed at the UI boundary."""
    from agent.redact import redact_sensitive_text

    message = f"✗ Update failed: {error}"[:8192]
    if isinstance(error, subprocess.CalledProcessError):
        details = []
        for value in (error.stderr, error.stdout):
            text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
            if text and text.strip() not in details:
                details.append(text.strip())
        if details:
            detail = "\n".join(details)[-8192:]
            if any(reason in detail.lower() for reason in
                   ("could not resolve", "failed to connect", "connection refused", "network is unreachable")):
                message += "\nNetwork unavailable; cannot reach the source remote."
            message += "\n" + detail
    return redact_sensitive_text(message, force=True, redact_url_credentials=True)
