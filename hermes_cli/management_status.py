"""Non-secret, read-only protection observations for the existing management status."""
from __future__ import annotations

from datetime import UTC, datetime
import os


def requester_status(scope: str) -> dict:
    """A status observation never asserts the human's consent to another operation."""
    return {"scope": scope, "owner_consent": "not_asserted", "raw_key_export": False,
            "passphrase_change": "refused" if scope == "operations_only" else "requires_explicit_owner_consent"}


def _limits(reader) -> dict:
    try:
        soft, hard = reader()
    except (OSError, ValueError):
        return {"observed": False, "soft_bytes": None, "hard_bytes": None,
                "soft_unlimited": None, "hard_unlimited": None}
    return {"observed": True, "soft_bytes": soft if soft >= 0 else None, "hard_bytes": hard if hard >= 0 else None,
            "soft_unlimited": soft < 0, "hard_unlimited": hard < 0}


def workspace_status() -> dict:
    from hermes_security.protected_workspace import (
        ProtectedWorkspaceUnavailable, protected_workspace_base, protected_workspace_status,
    )
    next_command = ["hermes", "config", "set", "security.protected_work_dir", "/absolute/ram-only/directory"]
    result = {"availability": "unavailable", "base": None, "ram_only_proven": False, "filesystem": "unknown",
              "assessment": "read_only_no_creation", "next_config_command": next_command}
    try:
        base = protected_workspace_base()
        proof = protected_workspace_status(base)
    except ProtectedWorkspaceUnavailable as exc:
        return {**result, "diagnostic": str(exc)}
    except (OSError, ValueError):
        return {**result, "availability": "unknown", "diagnostic": "protected workspace evidence could not be read"}
    usable = base.is_dir() and os.access(base, os.W_OK | os.X_OK)
    available = proof.proven and usable
    diagnostic = "RAM-only backing is not established for this base"
    if available:
        diagnostic = "RAM-only backing and a writable base were observed; creation is checked at use"
    elif not usable:
        diagnostic = "the configured base is not an accessible writable directory"
    return {**result, "availability": "available" if available else "unavailable", "base": str(base),
            "ram_only_proven": proof.proven, "filesystem": proof.filesystem,
            "diagnostic": diagnostic,
            "next_config_command": None if available else next_command}


def status(home) -> dict:
    from hermes_cli.config import load_env
    from hermes_cli.management_config import config_operation
    from hermes_cli.vault_gate import _owning_vault_home
    from hermes_platform.memory_protection import core_dump_limits, memlock_limits, swap_state
    from hermes_security.session_authority import has_borrowed_authority
    from hermes_security.vault import get_vault

    # Preserve the existing authenticated config/env readers and their fail-closed errors.
    config_operation(home, {"op": "config.get"})
    names = sorted(load_env())
    owner = get_vault(_owning_vault_home(home), allow_env_unlock=False)
    custody = owner.protection_status()  # metadata only: no master_key snapshot/export
    borrowed = has_borrowed_authority(home)
    core = _limits(core_dump_limits)
    return {
        "status_version": 2, "home": str(home), "unlocked": not custody["wiped"],
        "secret_names": names, "effect": "next_session",
        "observed_at": datetime.now(UTC).isoformat(),
        "resource_scope": {"profile_home": str(home), "vault_home": str(owner.home),
                           "authority": "borrowed_native" if borrowed else "local_credential",
                           "protection_observed_in": "responding_process"},
        "requester": requester_status("borrowed_native" if borrowed else "local_process"),
        "protection": {
            "retained_key": {"locked": custody["retained_key_locked"], "dontdump": custody["retained_key_dontdump"],
                             "wiped": custody["wiped"], "coverage": "retained_key_only"},
            "memlock_limits": _limits(memlock_limits),
            "swap": {"state": swap_state(), "encrypted_swap_evidence": "unknown",
                     "transient_buffers_unswappable": "not_established"},
            "core_dumps": {**core, "soft_limit_zero": core["soft_bytes"] == 0 if core["observed"] else None,
                           "piped_collector_policy": "unknown", "crash_store_encryption_evidence": "unknown"},
            "protected_work_dir": workspace_status(),
            "workflow_admission": "not_evaluated",
        },
    }
