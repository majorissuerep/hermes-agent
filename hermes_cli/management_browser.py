"""Explicit browser maintenance consumers share bounded domain ownership and locks."""
from __future__ import annotations


def perform(home, *, legacy=False):
    from hermes_state.runtime_ownership import ProfileOwnership
    from tools import browser_outgoing_recovery as recovery

    owner = ProfileOwnership()
    try:
        owner.reserve([home])  # cooperative sessions allowed; full restore remains excluded
        return (recovery.migrate_legacy if legacy else recovery.recover)(home)
    finally:
        owner.close()


def worker_repair(home):
    try:
        from hermes_cli.browser_worker_repair import repair_owned
    except ModuleNotFoundError as exc:
        if exc.name != "hermes_cli.browser_worker_repair":
            raise
        from hermes_cli.management_config import ManagementError
        raise ManagementError("unavailable", "This owning process lacks browser worker repair; update the owning runtime") from exc
    return repair_owned(home)
