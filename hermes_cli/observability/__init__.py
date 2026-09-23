"""First-party Hermes observability integrations."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def observe_lifecycle(hook_name: str, **kwargs: Any) -> None:
    """Dispatch a Hermes lifecycle event to built-in observability features.
    Fork: telemetry removed — no-op."""
    return None


def handles_hook(hook_name: str) -> bool:
    """Return whether any built-in observability feature handles a hook.
    Fork: telemetry removed — nothing handles hooks."""
    return False
