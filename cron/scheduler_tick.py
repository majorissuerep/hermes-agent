"""In-process tick admission and dispatch; job execution stays in scheduler.py."""

import concurrent.futures
import contextlib
import logging

from cron import store_health
from cron.constants import is_recurring

logger = logging.getLogger("cron.scheduler")

def tick(verbose=True, sync=True):
    """Execute due local jobs under the per-home lock; no service is started."""
    return _tick_admitted(verbose, sync)


def _acquire_tick_lock_or_degrade(_sched):
    """The tick lock fd, or None when this tick must not run: another ticker holds the lock, or
    the store cannot be written (read-only/full/denied: the lock file cannot even be created), in
    which case the store is marked degraded and the MCP orphan sweep still runs."""
    lock_dir, lock_file = _sched._get_lock_paths()
    _sched._ensure_cron_dir(lock_dir)
    try:
        return _sched._acquire_tick_lock(lock_file)
    except OSError as exc:
        if exc.errno not in store_health.UNWRITABLE_ERRNOS:
            raise  # EMFILE/ENFILE etc. stay a FAILED tick (#87644)
        store_health.note_unwritable(exc, "tick lock unavailable", "lock", cron_dir=lock_dir)
        _sched._sweep_mcp_orphans()
        return None


def _advance_or_drop_recurring(_sched, due_jobs: list) -> list:
    """Persist the recurring jobs' advance before any runs; when the store refuses it, drop them
    (no durable advance -> a crash mid-run would re-fire them, so skipping is the at-most-once
    side). One-shots still go through their own fire claim."""
    try:
        _sched.advance_next_runs([job["id"] for job in due_jobs])
        return due_jobs
    except OSError as exc:
        skipped = [j for j in due_jobs if is_recurring(j)]
        store_health.note_unwritable(exc, f"skipped {len(skipped)} recurring job(s)", "advance", skipped)
        return [j for j in due_jobs if not is_recurring(j)]


def _pre_dispatch_housekeeping(_sched) -> None:
    """Admission reconcile, legacy upgrade drain and dead-owner reap, run under the tick lock
    before due jobs are read."""
    _sched._maybe_reap_dead_owners()


def _tick_admitted(
    verbose: bool = True, sync: bool = True):
    """Run due local jobs under the home lock, returning the processed count."""
    from cron import scheduler as _sched

    lock_fd = _acquire_tick_lock_or_degrade(_sched)
    if lock_fd is None:
        return 0

    try:
        # `hermes pause` ESTOP: skip dispatch, never touch in-flight runs; check_paused logs once.
        with contextlib.suppress(ImportError):
            from agent.estop import check_paused as _estop_check_paused
            if _estop_check_paused("cron", _sched.logger):
                return 0

        _pre_dispatch_housekeeping(_sched)
        # Periodic worktree GC (6h, threaded) — the only sweep gateway-only boxes get.
        try:
            _sched._maybe_run_worktree_maintenance()
        except Exception as _wt_exc:
            _sched.logger.debug("Worktree maintenance dispatch failed: %s", _wt_exc)

        due_jobs = _sched.get_due_jobs()
        _sched._sweep_stale_inflight_for_tick(due_jobs)

        if not due_jobs:
            # Idle tick: skip config load + pool setup, but still reap crashed jobs' MCP orphans.
            if verbose:
                # Idle tick: skip config load + pool partitioning entirely (#33612 — the gateway ticker
                # calls tick(verbose=False) every 60s, so idle ticks previously fell through to
                # load_config()). Still run the post-tick MCP orphan sweep: main intentionally sweeps on
                # idle ticks so orphaned stdio children from crashed jobs are reaped even when nothing is
                # due.
                _sched.logger.info("%s - No jobs due", _sched._hermes_now().strftime('%H:%M:%S'))
            store_health.recheck_idle()
            _sched._sweep_mcp_orphans()
            return 0

        if store_health.dispatch_blocked(due_jobs):
            # Known-unwritable store, re-probed at most once a minute: every advance/claim would
            # fail, so skip them (the skip is recorded) and keep the tick's housekeeping alive.
            _sched._sweep_mcp_orphans()
            return 0

        if verbose:
            _sched.logger.info("%s - %s job(s) due", _sched._hermes_now().strftime('%H:%M:%S'), len(due_jobs))

        # Advance next_run_at for recurring jobs FIRST, under the lock, before any execution
        # (at-most-once). Re-advancing running jobs keeps the grace window alive; mark_job_run
        # overwrites it on completion. Composes with the claim-time advance in claim_job_for_fire.
        due_jobs = _advance_or_drop_recurring(_sched, due_jobs)
        if not due_jobs:
            _sched._sweep_mcp_orphans()
            return 0

        _max_workers = _sched._resolve_max_parallel_workers()
        if verbose:
            _sched.logger.info(
                "Running %d job(s) in parallel (max_workers=%s)",
                len(due_jobs),
                _max_workers if _max_workers else "unbounded")

        def _process_job(job: dict) -> bool:
            return _sched._process_due_job(job, verbose)

        # Persistent pool, non-blocking dispatch. Already-running jobs are skipped; mark_job_run
        # re-arms next_run_at on completion, so no catch-up queue is needed.
        _results: list = []
        _all_futures: list = []
        pool = _sched._get_parallel_pool(_max_workers)
        for job in due_jobs:
            fut = _sched._submit_with_guard(job, pool, _process_job)
            if fut is None:
                continue
            _all_futures.append(fut)
            if not sync:
                _results.append(True)  # optimistically counted

        if sync:
            for f in concurrent.futures.as_completed(_all_futures):
                try:
                    _results.append(f.result())
                except Exception as exc:
                    _sched.logger.error("Cron job future failed: %s", exc)
                    _results.append(False)
            _sched._sweep_mcp_orphans()
            return sum(_results)

        _sched._sweep_mcp_orphans_when_all_done(_all_futures)
        return sum(_results)
    finally:
        _sched._release_tick_lock(lock_fd)
