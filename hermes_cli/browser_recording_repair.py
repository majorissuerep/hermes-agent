"""Explicit repair: seal browser recordings whose recording process ended before they were saved.

A recording that failed to seal (locked vault, full disk, crash) keeps its only plaintext in a
stable proven-RAM directory named by an encrypted locator under ``artifacts/tool-results``
(``tools.browser_recording_pending``). ``hermes secure-vault repair`` calls :func:`recover_pending`
under the exclusive maintenance fence with this explicit repair's own fresh custody of exactly
this home. A capture is saved only when its locator authenticates, it belongs to this home and
vault, its recording process is PROVEN ended (PID/start-time identity), and the RAM directory,
manifest, exact allocated name, size and hash still match a cleanly completed recording. Every
other capture (still running, never stopped, foreign, replaced, unverifiable) is left exactly in
place and reported with its RAM path.
"""
from __future__ import annotations

from pathlib import Path

from hermes_security.errors import VaultError


def _shown(name: str) -> str:
    from tools.browser_recording_pending import LOCATOR_SUBDIR

    return Path(*LOCATOR_SUBDIR, name).as_posix()


def _recover_into(report: dict[str, list[str]], home: Path, name: str, custody) -> None:
    from tools import browser_recording_pending as pending

    try:
        destination, problem = pending.recover(home, name, custody)
    except (pending.PendingRecordingError, VaultError, OSError, ValueError) as exc:
        report["retained"].append(f"{_shown(name)} ({type(exc).__name__}: {exc})")
        return
    saved = destination.relative_to(home).as_posix()
    report["saved"].append(saved)
    if problem is not None:
        report["retained"].append(f"{_shown(name)} (saved encrypted; cleanup incomplete: {problem})")


def recover_pending(home: Path | str) -> dict[str, list[str]]:
    """Seal every recoverable pending browser recording of ``home``.

    Returns ``{"saved": [home-relative video, ...], "retained": ["<locator> (<reason>)", ...]}``.
    Root calls this only from explicit human repair; it never runs implicitly."""
    from hermes_security import artifacts
    from hermes_state.runtime_ownership import OwnershipConflict, exclusive_maintenance
    from tools import browser_recording_pending as pending

    home = Path(home).expanduser().resolve()
    report: dict[str, list[str]] = {"saved": [], "retained": []}
    try:
        names = pending.locator_names(home)
    except OSError as exc:
        report["retained"].append(f"{Path(*pending.LOCATOR_SUBDIR).as_posix()} ({type(exc).__name__}: {exc})")
        return report
    processed: set[str] = set()
    if not names:
        return report
    try:
        with exclusive_maintenance([home]):
            custody = artifacts._bound_custody(home)  # this explicit repair's fresh unlock, never a cached one
            for name in names:
                _recover_into(report, home, name, custody)
                processed.add(name)
    except (OwnershipConflict, VaultError, PermissionError) as exc:
        report["retained"] += [f"{_shown(name)} ({type(exc).__name__}: {exc})"
                               for name in names if name not in processed]
    return report
