"""Read-only key-output admission shared by pre-PM startup and vault creation.

Keep this module stdlib-only: invalid public migration arguments must not load
crypto or create config, logs, scratch, PM state, keys, or recovery archives.
"""
from __future__ import annotations

import os
from pathlib import Path


def key_destination(home: Path, output: str | None) -> Path:
    """Require a new external credential file before any migration/startup writes."""
    if not output:
        raise ValueError("--key-only requires --key-out naming a NEW file outside the Hermes home")
    requested = Path(output).expanduser().absolute()
    if requested.is_symlink() or requested.exists():
        raise ValueError("--key-out must name a new file; existing files and links are never overwritten")
    target = requested.resolve(strict=False)
    if (target.is_relative_to(Path(home).expanduser().resolve(strict=False))
            or any((parent / ".hermes-vault").is_file() for parent in target.parents)):
        raise ValueError("--key-out must be outside this home and every encrypted Hermes home")
    parent = target.parent
    while not parent.exists():
        parent = parent.parent
    access = {"effective_ids": True} if os.access in os.supports_effective_ids else {}
    if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK, **access):
        raise ValueError("--key-out parent must be a writable, searchable directory")
    return target
