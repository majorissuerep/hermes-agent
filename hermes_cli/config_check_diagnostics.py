"""Read-only diagnostics for local saved toolsets."""

from __future__ import annotations

from typing import Any


def config_check_diagnostics(config: dict[str, Any]) -> list[str]:
    from hermes_cli.toolset_validation import saved_toolset_resolver, validate_platform_toolsets

    return validate_platform_toolsets(config.get("platform_toolsets"), saved_toolset_resolver(config))
