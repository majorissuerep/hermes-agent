"""Direct agent tool invocation and its authorization boundary."""

from __future__ import annotations
import json
import logging
import time
from typing import Any, Dict, List, Optional
from agent import agent_runtime_helpers as runtime

logger = logging.getLogger(__name__)


def invoke_tool(agent, function_name: str, function_args: dict, effective_task_id: str,
                 tool_call_id: Optional[str] = None, messages: list = None,
                 pre_tool_block_checked: bool = False,
                 skip_tool_request_middleware: bool = False,
                 tool_request_middleware_trace: Optional[List[Dict[str, Any]]] = None,
                 skip_tool_execution_middleware: bool = False) -> str:
    """Invoke a single tool (agent-level or registry-dispatched) and return the result string;
    no display logic. Used by the concurrent path; the sequential path keeps its own inline
    invocation for display."""
    from agent.inline_tool_executors import (
        InlineToolContext, apply_transform_tool_result, emit_terminal_post_tool_call,
        resolve_invoke_tool_executor, tool_hook_ids
    )
    if not isinstance(function_args, dict):
        function_args = {}
    hook_ids = tool_hook_ids(agent, effective_task_id, tool_call_id)
    _tool_middleware_trace = list(tool_request_middleware_trace or [])
    try:
        from hermes_cli.middleware import apply_tool_request_middleware
        if not skip_tool_request_middleware:
            _tool_request_mw = apply_tool_request_middleware(function_name, function_args, **hook_ids)
            function_args = _tool_request_mw.payload
            _tool_middleware_trace = _tool_request_mw.trace
    except Exception as _mw_err:
        logger.debug("tool_request middleware error: %s", _mw_err)
    block_message: Optional[str] = None
    if not pre_tool_block_checked:
        block_message, function_args = runtime._pre_tool_block_message(
            agent, function_name, function_args, effective_task_id, tool_call_id, _tool_middleware_trace
        )
    if block_message is not None:
        result = json.dumps({"error": block_message}, ensure_ascii=False)
        emit_terminal_post_tool_call(
            agent, function_name=function_name, function_args=function_args, result=result,
            effective_task_id=effective_task_id, tool_call_id=tool_call_id, status="blocked",
            error_type="plugin_block", error_message=block_message,
            middleware_trace=_tool_middleware_trace,
        )
        return result
    tool_start_time = time.monotonic()
    inline_executor = resolve_invoke_tool_executor(agent, function_name)
    if inline_executor is not None:
        inline_ctx = InlineToolContext(
            effective_task_id=effective_task_id, tool_call_id=tool_call_id, messages=messages
        )

        def _execute(next_args: dict) -> Any:
            from agent.tool_policy import run_inline_call
            result = run_inline_call(function_name, next_args, lambda: inline_executor(agent, next_args, inline_ctx))
            call_args = next_args if isinstance(next_args, dict) else function_args
            duration_ms = int((time.monotonic() - tool_start_time) * 1000)
            emit_terminal_post_tool_call(
                agent, function_name=function_name, function_args=call_args,
                result=result, effective_task_id=effective_task_id, tool_call_id=tool_call_id,
                duration_ms=duration_ms, middleware_trace=_tool_middleware_trace,
            )
            return apply_transform_tool_result(
                agent, function_name=function_name, function_args=call_args, result=result,
                effective_task_id=effective_task_id, tool_call_id=tool_call_id, duration_ms=duration_ms,
            )
    else:
        def _execute(next_args: dict) -> Any:
            dispatch_kwargs = dict(
                tool_call_id=tool_call_id, session_id=agent.session_id or "",
                turn_id=getattr(agent, "_current_turn_id", "") or "",
                api_request_id=getattr(agent, "_current_api_request_id", "") or "",
                enabled_tools=list(agent.valid_tool_names) if agent.valid_tool_names else None,
                skip_pre_tool_call_hook=True, skip_tool_request_middleware=True,
                enabled_toolsets=getattr(agent, "enabled_toolsets", None),
                disabled_toolsets=getattr(agent, "disabled_toolsets", None),
                tool_request_middleware_trace=list(_tool_middleware_trace),
            )
            if skip_tool_execution_middleware:
                dispatch_kwargs["skip_tool_execution_middleware"] = True
            import model_tools
            return model_tools.handle_function_call(function_name, next_args, effective_task_id, **dispatch_kwargs)
    if not pre_tool_block_checked:
        from agent.policy_sidecars import get_sidecars
        from agent.tool_policy import policy_scope
        sidecars = get_sidecars(agent)
        if sidecars.guard:
            history = messages if messages is not None else getattr(agent, "_session_messages", [])
            task = next((row.get("content", "") for row in reversed(history) if row.get("role") == "user"), "")
            if not sidecars.guard.task:
                from agent.sidecar_client import text_content
                sidecars.guard.update_task(text_content(task), history)
        unreviewed_execute = _execute

        def _execute(next_args):
            if sidecars.guard:
                blocked = sidecars.guard.review(function_name, next_args)
                if blocked is not None:
                    return blocked
            with policy_scope(sidecars.guard, reviewed_name=function_name, reviewed_args=next_args):
                return unreviewed_execute(next_args)

    if skip_tool_execution_middleware:
        return _execute(function_args)
    from hermes_cli.middleware import run_tool_execution_middleware
    return run_tool_execution_middleware(
        function_name, function_args,
        lambda next_args: _execute(next_args if isinstance(next_args, dict) else function_args),
        original_args=function_args, **hook_ids,
    )
