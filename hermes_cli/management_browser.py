"""Explicit browser maintenance consumers share bounded domain ownership and locks."""
from __future__ import annotations


def proxy_current_request(request, home):
    """Only fixed repair requests reuse the native client's existing bounded jobs."""
    import json
    import os
    from hermes_security.session_authority import ENV_NAME, _vault_home
    from hermes_security.vault import is_unlocked

    if (not isinstance(request, dict) or not isinstance(request.get("op"), str)
            or request.get("op") not in {"browser.outgoing.repair", "browser.worker.repair"}
            or not os.environ.get(ENV_NAME) or os.environ.get("HERMES_VAULT_KEY_FD")):
        return None
    owner = _vault_home(home)
    if owner is None or is_unlocked(owner):
        return None
    from hermes_cli.management import _request
    from hermes_cli.session_proxy import main
    _request(request)
    # management.main already consumed this exact JSON request. Never reread stdin,
    # change arbitrary interactive commands, or send a long synchronous manage frame.
    return main(["manage", "apply"], input_data=json.dumps(request).encode("utf-8"))


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
