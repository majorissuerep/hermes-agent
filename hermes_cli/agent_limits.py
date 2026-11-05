"""Turn-limit precedence shared by interactive and headless native owners."""

from __future__ import annotations

import os
from typing import Any


def configured_turn_limit(config: dict[str, Any], override: Any = None) -> int:
    from hermes_cli.config import resolve_turn_limit

    section = config.get("agent")
    agent = section if isinstance(section, dict) else {}
    raw = next((value for value in (override, agent.get("max_turns"), config.get("max_turns"))
                if value is not None), os.getenv("HERMES_MAX_ITERATIONS"))
    return resolve_turn_limit(raw)
