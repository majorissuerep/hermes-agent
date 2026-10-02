"""Per-agent policy and routing state, snapshotted under the owning profile scope."""

from __future__ import annotations

import logging
import os

from agent.sidecar_client import text_content

logger = logging.getLogger(__name__)


class PolicySidecars:
    def __init__(self, config: dict, tools=None, workspace=None):
        guard = (config.get("approvals") or {}).get("guard") or {}
        routing = config.get("smart_model_routing") or {}
        auxiliary = config.get("auxiliary") or {}
        self.guard = self.router = None
        if guard.get("enabled") is True:
            from agent.tool_policy import ToolPolicy
            from agent.runtime_cwd import scope_terminal_cwd
            from tools.terminal_scope import terminal_env

            terminal = config.get("terminal") or {}
            self.guard = ToolPolicy(
                guard, auxiliary.get("tool_guard") or {},
                workspace or scope_terminal_cwd() or terminal.get("cwd") or os.getcwd(),
                terminal_env("TERMINAL_ENV", terminal.get("env_type", "local")),
                schemas={row["function"]["name"]: row["function"] for row in (tools or [])
                         if row.get("type") == "function"},
            )
        if routing.get("enabled") is True:
            from agent.model_router import ModelRouter
            try:
                self.router = ModelRouter(routing, auxiliary.get("model_router") or {})
            except (ValueError, TypeError) as exc:
                logger.warning("Invalid model routing pool: %s; routing disabled for this session", exc)

    def prepare(self, agent, messages: list, task, *, current_turn_user_idx=None) -> None:
        task = text_content(task)
        from agent.prompt_builder import STEER_DISPLAY_KIND
        current_messages = messages[current_turn_user_idx:] if isinstance(current_turn_user_idx, int) else []
        steer = next((text_content(row.get("content")) for row in reversed(current_messages)
                      if row.get("display_kind") == STEER_DISPLAY_KIND), "")
        if steer and steer != task:
            task += "\nLatest authorized steer:\n" + steer
        if self.guard:
            self.guard.update_task(task, messages)
        if self.router:
            self.router.prepare(agent, task, messages)


def get_sidecars(agent) -> PolicySidecars:
    sidecars = getattr(agent, "_policy_sidecars", None)
    if sidecars is None:
        from hermes_cli.config import load_config_readonly
        sidecars = PolicySidecars(load_config_readonly(), getattr(agent, "tools", []),
                                  workspace=getattr(agent, "session_cwd", None))
        agent._policy_sidecars = sidecars
    return sidecars
