"""Explicit encrypted-state repair and quiescent recording recovery through the existing CLI."""
from __future__ import annotations

from hermes_security import vault as vault_mod


def run(args, home) -> int:
    if not vault_mod.vault_exists(home):
        print(f"✗ No vault at {home}; run 'hermes secure-vault migrate' first")
        return 1
    from hermes_cli.vault_cmd import gate_locked_vault

    gate_locked_vault(args, home)
    from hermes_security.migrate import repair_clobbered_state
    from hermes_cli.browser_recording_repair import recover_pending
    from hermes_state.runtime_ownership import OwnershipConflict, exclusive_maintenance

    try:
        with exclusive_maintenance([home]):
            report = repair_clobbered_state(home)
            recordings = recover_pending(home)
    except OwnershipConflict as exc:
        print(f"✗ Repair requires the owning Hermes session to be closed: {exc}")
        return 1
    report["recording_saved"], report["recording_retained"] = recordings["saved"], recordings["retained"]
    sections = (
        ("sealed", "✓ Re-sealed {n} plaintext file(s) into envelopes:"),
        ("unwrapped", "✓ Unwrapped {n} database/stream file(s) a previous repair wrapped in envelopes:"),
        ("reframed", "✓ Re-framed {n} log/transcript stream(s) holding plaintext:"),
        ("restored", "✓ Restored {n} mangled file(s) from the pre-migration backup:"),
        ("skipped_dbs", "⚠ {n} PLAINTEXT database(s) left untouched (vault bypass suspected — investigate):"),
        ("unrecoverable", "✗ {n} file(s) could not be repaired:"),
        ("browser_archived", "✓ Moved {n} plaintext browser profile(s) into verified encrypted archives "
                             "(old copies removed; no SSD secure-erase is claimed):"),
        ("browser_retained", "✗ {n} browser profile(s) retained (see recovery details):"),
        ("recording_saved", "✓ Saved {n} completed browser recording(s) encrypted:"),
        ("recording_retained", "✗ {n} browser recording(s) still require recovery or cleanup:"),
    )
    for key, title in sections:
        items = report[key]
        if not items:
            continue
        print(title.format(n=len(items)))
        for rel in items[:20]:
            print(f"    {rel}")
        if len(items) > 20:
            print(f"    … and {len(items) - 20} more")
    if report["code_files"]:
        print(f"✓ Restored {report['code_files']} file(s) in hermes-agent.* code trees to plaintext (public code)")
    if not any(report[key] for key, _ in sections) and not report["code_files"]:
        print("○ Nothing to repair (every state file is in its canonical sealed form)")
    if any(report[key] for key in ("unrecoverable", "skipped_dbs", "browser_retained", "recording_retained")):
        return 1
    return 0

