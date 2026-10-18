"""Prepare an interpreter that can run the icon generator without a Hermes install.

Website branding builders use the committed build-only icons extra in an isolated
PM environment, without adding image renderers to the terminal runtime.
"""
from __future__ import annotations

from pathlib import Path
import sys

# Builders import this file from a separately prepared source tree.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pm


def prepare_icon_environment(source: Path, out: Path, cache: Path | None) -> Path:
    """Prepare a fresh interpreter with the locked build-only icons dependency selection."""
    return pm.build_environment(
        source=source.resolve(), out=out.resolve(), cache=cache.resolve() if cache else None,
        no_install_project=True, extras=["icons"], explicit=True,
    )
