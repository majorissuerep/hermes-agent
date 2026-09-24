"""Default-deny model tools: a sandboxed session exposes only the tools its policy names.

An allowlist entry is a tool name (``read_file``), a toolset (``file``, ``web``,
``mcp-github``) or ``*`` (everything — an explicit opt-out). Enforcement is twofold:
the schema filter (:func:`filter_tool_definitions`, applied once when the agent's tool
list is built — so the prompt cache is never broken mid-conversation) and the dispatch
check (:func:`tool_block_reason`, applied to every call, so a tool re-added later by an
MCP refresh or a provider still cannot run).
"""

from __future__ import annotations

from typing import Iterable, Optional

from hermes_security.sandbox.policy import SandboxPolicy


def _toolset_of(name: str) -> str:
    try:
        from tools.registry import registry
        return registry.get_toolset_for_tool(name) or ""
    except Exception:
        return ""


def _expand(entry: str) -> set[str]:
    try:
        from toolsets import resolve_toolset
        return set(resolve_toolset(entry))
    except Exception:
        return set()


def allowed_names(policy: SandboxPolicy, candidates: Iterable[str]) -> set[str]:
    entries = set(policy.tools)
    if "*" in entries:
        return set(candidates)
    expanded = set(entries)
    for entry in entries:
        expanded |= _expand(entry)
    allowed = {n for n in candidates if n in expanded or _toolset_of(n) in entries}
    if allowed:
        from tools.tool_search_catalog import BRIDGE_TOOL_NAMES
        allowed |= set(BRIDGE_TOOL_NAMES) & set(candidates)
    return allowed


def _policy() -> Optional[SandboxPolicy]:
    from hermes_security.sandbox.policy import is_enabled
    if not is_enabled():
        return None
    from hermes_security.sandbox.session import effective_policy
    return effective_policy(with_tmp=False)


def filter_tool_definitions(tools: list[dict]) -> list[dict]:
    policy = _policy()
    if policy is None:
        return tools
    names = [t["function"]["name"] for t in tools]
    keep = allowed_names(policy, names)
    return [t for t in tools if t["function"]["name"] in keep]


def tool_block_reason(name: str) -> Optional[str]:
    policy = _policy()
    if policy is None or name in allowed_names(policy, [name]):
        return None
    from tools.tool_search_catalog import BRIDGE_TOOL_NAMES
    if name in BRIDGE_TOOL_NAMES and policy.tools:
        return None  # the bridge's underlying call is checked again after unwrapping
    return (f"Tool '{name}' is not allowed in this sandboxed session. The user can allow it "
            f"with '/sandbox tools add {name}' (or a toolset / preset) — takes effect next session.")
