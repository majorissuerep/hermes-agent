"""Explicit offline checkpoint of quiescent RAM profiles before disaster recovery."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from hermes_cli import browser_profile_store as store
from hermes_security import browser_profile_archive as archive
from hermes_security.errors import VaultError


def _recover(profile, custody) -> str:
    """Acquire a NEW request lease; no capability from the old browser owner is borrowed."""
    with store._profile_lock(profile, wait=0.0):
        store._directory_identity(profile.path)
        binding = store._owned_binding(profile, profile.path)
        busy = store._busy_reason(profile.path, binding, own_claim_busy=True)
        if busy:
            raise store.BrowserProfileError(f"{busy}; close that browser and its Hermes session, then retry repair")
        if binding.get("consent_revoked"):
            raise store.BrowserProfileError("Consent was revoked; the RAM copy was retained for explicit cleanup")
        identity = binding.get("identity_doc")
        if not isinstance(identity, dict) or archive.identity_digest(custody, profile.slot, identity) != binding.get("identity"):
            raise store.BrowserProfileError("RAM browser identity cannot be authenticated; every copy was retained")
        if binding.get("archive") != archive.archive_relative(profile.slot, binding["identity"]).as_posix():
            raise store.BrowserProfileError("RAM browser archive location is invalid; every copy was retained")
        token = secrets.token_hex(16)
        custody.check()
        store._write_binding(profile.path, {**binding, "lease": token, "claimed_at": None,
                                           "launcher": None, "persisted": False})
        lease = store._remember(profile, custody, token)
        outcome = store._settle_lease(lease)
        if outcome != "saved":
            raise store.BrowserProfileError(f"RAM profile was not retired ({outcome}); retry after checking vault status")
    return profile.slot


def recover_quiescent_profiles(home: Path) -> dict[str, list[str]]:
    """Called only by explicit secure-vault repair; save before retiring any sole RAM copy."""
    from hermes_cli.backup_browser_profiles import profiles
    from hermes_state.runtime_ownership import OwnershipConflict, exclusive_maintenance

    report = {"archived": [], "retained": []}
    try:
        try:
            candidates = list(profiles(home))
        except store.BrowserProfileError:
            if not archive.archive_paths(home):
                return report  # no known browser state and no configured RAM workspace
            raise
        with exclusive_maintenance([home]):
            for profile in candidates:
                if not os.path.lexists(profile.path):
                    continue
                try:
                    custody = archive.capture_custody(home)
                    report["archived"].append(f"RAM {_recover(profile, custody)}")
                except (store.BrowserProfileError, OSError, VaultError) as exc:
                    report["retained"].append(f"RAM {profile.slot} ({exc})")
    except (OwnershipConflict, store.BrowserProfileError, OSError, VaultError) as exc:
        report["retained"].append(f"RAM profiles could not be checked ({exc})")
    return report
