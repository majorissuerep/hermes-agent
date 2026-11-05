"""Explicit offline adoption of complete legacy machine-profile frame streams.

Runtime readers never guess that an unscoped machine-vault stream belongs to a
particular profile. This deliberate maintenance action assigns that old stream
at its selected path, preserving the original ciphertext before conversion.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat

from hermes_security.errors import VaultError, VaultIntegrityError

_BOUND = 64 * 1024 * 1024


def _stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _read(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                or info.st_mode & 0o077 or info.st_size > _BOUND):
            raise VaultIntegrityError("Legacy profile stream is not an owned bounded private file")
        raw = source.read(_BOUND + 1)
    if len(raw) > _BOUND or _stamp(path.lstat()) != _stamp(info):
        raise VaultIntegrityError("Legacy profile stream changed during verification")
    return raw, _stamp(info)


def _purpose(path, relative):
    from hermes_security.artifact_paths import terminal_frame_purpose
    from hermes_security.migrate import _frame_purpose

    return terminal_frame_purpose(relative) or _frame_purpose(path)


def _rebind(custody, path, relative):
    from hermes_security import artifacts, frames

    custody.check_target(path)
    with frames.stream_lock(path):
        raw, identity = _read(path)
        purpose = _purpose(path, relative)
        try:
            frames._complete_stream(raw, vault=custody, purpose=purpose)
            return False
        except VaultIntegrityError:
            # This is ONLY an explicit offline command, never a runtime reader fallback.
            payloads = frames._complete_stream(raw, vault=custody._resource, purpose=purpose)
        encoded = frames.encode_frames(payloads, vault=custody, purpose=purpose)
        if frames._complete_stream(encoded, vault=custody, purpose=purpose) != payloads:
            raise VaultIntegrityError("Scoped legacy stream verification failed")
        backup = Path("backups/profile-frames") / relative.parent / (
            relative.name + "." + hashlib.sha256(raw).hexdigest() + ".cipher")
        custody.check_target(custody.home / backup)
        with artifacts._parent(custody.home, backup, create=True):
            pass
        try:
            previous = artifacts._read_owned_bytes(custody.home, backup, _BOUND + 128)
        except FileNotFoundError:
            preserved = custody.encrypt(raw, purpose="state", relpath=backup.as_posix())
            artifacts._publish(custody.home, backup, preserved, custody)
        else:
            if custody.decrypt(previous, purpose="state", relpath=backup.as_posix()) != raw:
                raise VaultIntegrityError("Original encrypted stream backup conflicts; source retained")
        custody.check_target(path)
        if _stamp(path.lstat()) != identity:
            raise VaultIntegrityError("Legacy stream changed before publication; every copy retained")
        artifacts._publish(custody.home, relative, encoded, custody, overwrite=True)
        return True


def repair_profile_frames(home):
    """Hold the existing exclusive selected-profile fence; preserve every unknown input."""
    from hermes_security.migrate import _classify, _iter_files
    from hermes_security.profile_binding import capture
    from hermes_state.runtime_ownership import exclusive_maintenance

    custody = capture(home)
    if custody.home == custody.vault.home:
        raise ValueError("This operation only rebinds machine-owned named profile streams")
    report = {"rebound": [], "retained": []}
    with exclusive_maintenance([custody.home]):
        custody.check()
        for path, relative in _iter_files(custody.home, exclude_roots={"backups", "state-snapshots", "profiles"}):
            if _classify(path, relative) != "frames":
                continue
            try:
                if _rebind(custody, path, relative):
                    report["rebound"].append(relative.as_posix())
            except (OSError, ValueError, VaultError) as error:
                report["retained"].append(f"{relative.as_posix()}: {error}")
    return report


def cmd_parent_profile_repair(args, home):
    """Existing repair CLI: only the authenticated selected parent-owned profile."""
    from hermes_cli.vault_cmd import gate_locked_vault
    from hermes_security.profile_binding import owner_hint
    from hermes_state.runtime_ownership import exclusive_maintenance

    home = Path(home).resolve()
    if os.path.lexists(home / ".hermes-vault"):
        return None  # Own-vault repair retains its existing broader format repair consumer.
    try:
        owner = owner_hint(home)
        with exclusive_maintenance([home]):
            gate_locked_vault(args, owner)
            report = repair_profile_frames(home)
    except (OSError, ValueError, VaultError) as error:
        print(f"✗ Selected profile frame repair refused; existing state retained: {error}")
        return 1
    print(f"✓ Scoped {len(report['rebound'])} complete legacy profile stream(s).")
    print("  Original ciphertext retained in this profile's backups/profile-frames directory.")
    for item in report["retained"]:
        print(f"  Retained: {item}")
    return 1 if report["retained"] else 0


def _cmd_own_repair(args, home):
    from hermes_state.runtime_ownership import exclusive_maintenance

    with exclusive_maintenance([home]):
        return _repair_own_contents(args, home)


def _repair_own_contents(args, home):
    from hermes_cli.vault_cmd import gate_locked_vault
    from hermes_security import vault as vault_mod

    if not vault_mod.vault_exists(home):
        print(f"✗ No vault at {home}; run 'hermes secure-vault migrate' first")
        return 1
    gate_locked_vault(args, home)
    from hermes_security.migrate import repair_clobbered_state

    report = repair_clobbered_state(home)
    sections = (
        ("sealed", "✓ Re-sealed {n} plaintext file(s) into envelopes:"),
        ("unwrapped", "✓ Unwrapped {n} database/stream file(s) a previous repair wrapped in envelopes:"),
        ("reframed", "✓ Re-framed {n} log/transcript stream(s) holding plaintext:"),
        ("restored", "✓ Restored {n} mangled file(s) from the pre-migration backup:"),
        ("skipped_dbs", "⚠ {n} PLAINTEXT database(s) left untouched (vault bypass suspected — investigate):"),
        ("unrecoverable", "✗ {n} file(s) could not be repaired:"),
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
    if report["unrecoverable"] or report["skipped_dbs"]:
        return 1
    return 0


def cmd_repair(args, home):
    result = cmd_parent_profile_repair(args, home)
    return _cmd_own_repair(args, home) if result is None else result
