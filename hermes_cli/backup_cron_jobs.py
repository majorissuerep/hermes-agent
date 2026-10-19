"""Recover cron records lost during the current source update's migration."""

import logging
from pathlib import Path

from hermes_constants import get_hermes_home
from hermes_security import io as state_io
from hermes_cli.backup_cron_prompts import _cron_jobs_list, _load_cron_jobs_doc

logger = logging.getLogger("hermes_cli.backup")


def restore_cron_jobs_if_emptied(snapshot_id: str, hermes_home: Path | None = None) -> dict | None:
    """Restore a readable same-update snapshot only when the live job count fell.

    Missing or malformed live state is not an empty schedule. Read encrypted
    snapshots under their canonical identity, including historical whole-home
    migrations, and recheck under the owning cron writer lock before publishing.
    """
    if not snapshot_id:
        return None
    from cron import jobs as cron_jobs
    from hermes_cli.backup import _CRON_JOBS_REL, _quick_snapshot_root

    home = hermes_home or get_hermes_home()
    live_path = home / _CRON_JOBS_REL
    live = _load_cron_jobs_doc(live_path)
    if live is None:
        return None
    snap_path = _quick_snapshot_root(home) / snapshot_id / _CRON_JOBS_REL
    snapshot = _load_cron_jobs_doc(snap_path, canonical_path=live_path)
    snap_count = len(_cron_jobs_list(snapshot))
    if not snap_count or len(_cron_jobs_list(live)) >= snap_count:
        return None

    try:
        with cron_jobs.use_cron_store(home), cron_jobs._jobs_lock():
            live = _load_cron_jobs_doc(live_path)
            if live is None:
                return None
            live_count = len(_cron_jobs_list(live))
            if live_count >= snap_count:
                return None
            state_io.write_json(live_path, snapshot, purpose="state")
    except OSError as exc:
        logger.error("Cron jobs were lost during update but auto-restore failed: %s", exc)
        return None
    logger.warning(
        "Restored %d cron job(s) from pre-update snapshot %s "
        "(live file had %d job(s), snapshot had %d — jobs were lost during migration)",
        snap_count, snapshot_id, live_count, snap_count,
    )
    return {"restored": True, "job_count": snap_count, "snapshot_id": snapshot_id}
