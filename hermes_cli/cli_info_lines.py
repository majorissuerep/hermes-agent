"""The classic CLI's `/toolsets` and `/platforms` reports as plain lines.

Shared by the classic CLI (`CLIInfoMixin.show_toolsets` / `_show_gateway_status`) and the shared
gateway, which runs the same report for a local session's `slash.exec` (gateway/session_info_reads.py):
one text on every surface, read where the state lives (the session's toolsets, the gateway's
platform config).
"""
from __future__ import annotations

from agent.i18n import t


def _box(title: str, width: int) -> list[str]:
    pad = width - len(title)
    return ["+" + "-" * width + "+", "|" + " " * (pad // 2) + title + " " * (pad - pad // 2) + "|",
            "+" + "-" * width + "+"]


def toolsets_lines(enabled) -> list[str]:
    """``/toolsets``: every toolset, ``(*)`` on the ones *enabled* names."""
    from toolsets import get_all_toolsets, get_toolset_info
    out = ["", *_box(t("cli.toolsets.header"), 58), ""]
    for name in sorted(get_all_toolsets()):
        info = get_toolset_info(name)
        if info:
            marker = "(*)" if enabled and name in enabled else "   "
            out.append(f"  {marker} {name:<18} [{info['tool_count']:>2} tools] - {info['description']}")
    return [*out, "", f"  {t('cli.toolsets.currently_enabled_legend')}", "",
            f"  {t('cli.toolsets.tip_enable_all')}", f"  {t('cli.toolsets.example')}", ""]
