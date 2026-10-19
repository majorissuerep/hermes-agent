"""Mid-run plugin activation: what a just-loaded plugin does NOW vs next session (#87770).

One seam for every surface: ``PluginManager.on_plugin_loaded`` fires with one
:func:`plugin_activation_summary` per loaded plugin. Native commands and hooks are live as soon as
the plugin loads; portable MCP servers and skills go live in
the open chats of the plugin's profile (:func:`load_and_go_live`; MCP tools are deferred behind
tool_search, so the cached prompt prefix is unchanged); Python tools and system-prompt sections stay
deferred to the next session (prompt-cache invariant, same as ``/skills install``).
:func:`activate_plugin_now` is what the install / enable / update surfaces call after a successful
config or tree change.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_GO_LIVE_LOCK = threading.Lock()

def plugin_activation_summary(manager: Any, plugin_key: str) -> dict[str, Any]:
    """``{name, key, activated_now: {kind: [names]}, deferred: {kind: [names]}}`` for one loaded plugin,
    read from what it actually registered (ownership ledger + handler registries), not from what its
    manifest promises. Keys appear only when non-empty.

    ``activated_now``: ``commands`` (native slash names), ``hooks`` (hook names), ``locales``
    (``<lang>.<surface>`` language-pack layers).
    ``deferred``: ``tools`` (tool names; next session), ``prompt`` (section ids; next session),
    ``mcp_servers`` (the plugin's mcp.json server names exactly as registered; until ``mcp.reload``)."""
    loaded = manager._plugins.get(plugin_key)
    manifest = getattr(loaded, "manifest", None)
    name = getattr(manifest, "name", None) or plugin_key
    regs = [r for r in manager._ownership_ledger.get(plugin_key, []) if getattr(r, "active", True)]
    kinds: dict[str, list[str]] = {}
    for reg in regs:
        kinds.setdefault(reg.kind, []).append(str(reg.key))
    now: dict[str, list[str]] = {}
    if kinds.get("command"):
        now["commands"] = sorted(kinds["command"])
    hooks = set(kinds.get("hook", ()))
    if hooks:
        now["hooks"] = sorted(hooks)
    if kinds.get("locale"):  # language-pack layers: the i18n caches reset on registration, so live at once
        now["locales"] = sorted(kinds["locale"])
    deferred: dict[str, list[str]] = {}
    tools = sorted(set(kinds.get("tool", ())) | set(getattr(loaded, "tools_registered", None) or ())
                   | set(getattr(manifest, "provides_tools", None) or ()))
    if tools:
        deferred["tools"] = tools
    if kinds.get("system_prompt_section"):
        deferred["prompt"] = sorted(kinds["system_prompt_section"])
    servers = sorted(set(kinds.get("portable_mcp", ())) | {
        s for s, owner in manager._portable_mcp_server_plugins.items() if owner == plugin_key})
    if servers:
        deferred["mcp_servers"] = servers
    return {"name": name, "key": plugin_key, "activated_now": now, "deferred": deferred}


def activation_summaries(manager: Any) -> list[dict[str, Any]]:
    """One summary per loaded, non-errored plugin — the ``on_plugin_loaded`` payload."""
    out = []
    for key, loaded in list(manager._plugins.items()):
        if getattr(loaded, "error", None):
            continue
        out.append(plugin_activation_summary(manager, key))
    return out


def find_activation(summaries: Optional[list[dict[str, Any]]], name: str) -> Optional[dict[str, Any]]:
    """The summary for ``name`` (manifest name, canonical key, or bare leaf of the key)."""
    for entry in summaries or ():
        key = str(entry.get("key") or "")
        if name in (entry.get("name"), key, key.rsplit("/", 1)[-1]):
            return entry
    return None


def activate_plugin_now(name: str, *, in_process: bool = True) -> dict[str, Any]:
    """Load permitted runtime-local capabilities; other terminals adopt them next session."""
    activation = load_and_go_live(name) if in_process else None
    return {"activation": activation, "restart_required": not in_process}


def load_and_go_live(name: str) -> Optional[dict[str, Any]]:
    """Force-rediscover plugins in THIS process under the caller's profile scope, connect ``name``'s
    MCP servers and hand them (and its skills) to this profile's open chats with a turn note. Returns
    the activation summary with ``live_now: {mcp_servers, skills}``; ``deferred`` then keeps only what
    waits for the next session (Python ``tools``, ``prompt``). None when the plugin did not load."""
    # A forced rediscovery unloads every plugin before it loads them again, which drops their server
    # configs, skills and liveness declarations for the length of the pass. Two installs finishing
    # together (one card, two rows) each go live; the second one's pass must not run while the first
    # reads or connects, or the first plugin comes up with no tools. One go-live at a time.
    with _GO_LIVE_LOCK:
        return _go_live(name)


def _go_live(name: str) -> Optional[dict[str, Any]]:
    from hermes_cli.plugins_activation_live import connect_plugin_mcp, live_notice, plugin_skills
    try:
        from hermes_cli.plugins import _join_background_discovery, get_plugin_manager
        _join_background_discovery()
        manager = get_plugin_manager()
        # Other forced discovery passes do not take the go-live lock, so the
        # reads share the discovery lock with the pass that produced them.
        with manager._discovery_lock:
            manager.discover_and_load(force=True)
            activation = find_activation(activation_summaries(manager), name)
            portable = manager.get_portable_mcp_servers()
            skills = plugin_skills(activation["key"]) if activation else []
    except Exception:
        logger.debug("in-process plugin reload after change to %r failed", name, exc_info=True)
        return None
    if activation is None:
        return None
    servers = connect_plugin_mcp(activation, portable)
    activation["live_now"] = {"mcp_servers": servers, "skills": skills}
    activation["deferred"] = {k: v for k, v in (activation.get("deferred") or {}).items() if k != "mcp_servers"}
    import sys
    server = sys.modules.get("tui_runtime.server")  # loaded == this process hosts chats
    note = live_notice(activation)
    if server is not None and (servers or note):
        from hermes_constants import get_hermes_home
        try:
            server.refresh_plugin_sessions(Path(get_hermes_home()), activation["key"], note)
        except Exception:
            logger.warning("open chats were not refreshed after activating %r", name, exc_info=True)
    return activation






def activation_hint(result: dict[str, Any]) -> str:
    """One honest sentence for CLI surfaces from an :func:`activate_plugin_now` result."""
    act = result.get("activation") or {}
    live = act.get("live_now") or {}
    connected = [s for s in live.get("mcp_servers") or () if s.get("connected")]
    lines = []
    if connected or live.get("skills"):
        tools = sum(len(s.get("tools") or ()) for s in connected)
        lines.append(f"Live in open chats now: {tools} MCP tools, {len(live.get('skills') or ())} skills.")
    lines.extend(f"MCP server {s['name']} not connected: {s.get('error') or 'unknown error'}"
                 for s in live.get("mcp_servers") or () if not s.get("connected"))
    return "\n".join(lines or ["Available in the next terminal session."])
