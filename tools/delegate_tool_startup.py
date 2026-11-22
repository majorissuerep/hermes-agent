"""Bound admission of a native child before the runtime watchdog takes over."""

from concurrent.futures import Future, TimeoutError as FutureTimeout
from contextvars import Context, copy_context
from threading import Event
from time import monotonic
from typing import Any

from agent.memory_provider import spawn_context_thread
from tools.daemon_pool import DaemonThreadPoolExecutor
from tools.delegate_tool_child_run import _close_child
from tools.delegate_tool_config import DEFAULT_CHILD_IDLE_SECONDS

_STARTUP_WORKER = DaemonThreadPoolExecutor(max_workers=1, thread_name_prefix="subagent-startup")


class ChildStartupError(ValueError):
    """The parent did not admit a child to run."""


def _discard_startup_result(completed: Future, scope: Context) -> None:
    if not completed.cancelled() and completed.exception() is None:
        scope.run(spawn_context_thread, _close_child, name="subagent-startup-cleanup",
                  args=(completed.result(), "Failed to close child after startup was abandoned")).start()


def _await_startup(future: Future, parent: Any, index: int, timeout: float) -> Any:
    deadline = monotonic() + timeout
    while not future.done():
        if getattr(parent, "_interrupt_requested", False) is True:
            raise ChildStartupError(f"Subagent {index + 1} startup was interrupted.")
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise ChildStartupError(f"Subagent {index + 1} startup timed out after {timeout:g}s.")
        try:
            return future.result(timeout=min(remaining, 0.5))
        except FutureTimeout:
            continue
    return future.result()


def build_child_with_watchdog(**kwargs: Any) -> Any:
    """Start in the owning profile and close a late result without publishing it."""
    from tools.delegate_tool import _build_child_preserving_parent_tools, _get_child_timeout

    timeout = min(_get_child_timeout() or DEFAULT_CHILD_IDLE_SECONDS, DEFAULT_CHILD_IDLE_SECONDS)
    cancelled = Event()
    cleanup_scope = copy_context()
    parent, index = kwargs["parent_agent"], kwargs["task_index"]
    if callable(emit_status := getattr(parent, "_emit_status", None)):
        emit_status(f"Starting subagent {index + 1} of {kwargs['task_count']}…")
    future = _STARTUP_WORKER.submit(_build_child_preserving_parent_tools, **kwargs, startup_cancelled=cancelled)
    try:
        return _await_startup(future, parent, index, timeout)
    except ChildStartupError:
        cancelled.set()
        future.cancel()
        future.add_done_callback(lambda completed: _discard_startup_result(completed, cleanup_scope))
        raise
