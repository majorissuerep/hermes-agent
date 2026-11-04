"""One fixed outgoing repair consumer; public commands share its bounded domain locks."""
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
    from hermes_cli.browser_worker_repair import repair_owned
    return repair_owned(home)
