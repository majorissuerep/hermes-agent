"""Explicit outgoing recovery through the existing secure-vault repair command.

Default offline repair never invokes this consumer or connects to a target.
"""
from __future__ import annotations

from hermes_security.errors import VaultError


def run(home, *, legacy: bool) -> int:
    from tools import browser_outgoing_recovery as recovery

    try:
        report = (recovery.migrate_legacy if legacy else recovery.recover)(home)
    except (RuntimeError, VaultError, OSError, ValueError) as exc:
        print(f"✗ Outgoing browser state retained ({type(exc).__name__}: {exc})")
        return 1
    for path in report["saved"]:
        print(f"✓ Verified encrypted outgoing browser checkpoint: {path}")
    for detail in report["retained"]:
        print(f"✗ Outgoing browser state retained: {detail}")
    if legacy and report["saved"]:
        print("Old raw target files were retired after verification; no SSD secure-erase is claimed.")
    if not report["saved"] and not report["retained"]:
        print("○ No pending outgoing browser references in this selected home")
    return int(bool(report["retained"]))
