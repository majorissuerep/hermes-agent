"""Browser execution in the explicitly selected outgoing terminal backend."""
from __future__ import annotations

import hashlib
from typing import Any


def terminal_backend() -> str:
    from tools.terminal_tool import _get_env_config
    return str(_get_env_config().get("env_type") or "local").strip().lower()


def browser_in_terminal() -> bool:
    return terminal_backend() != "local"


def terminal_environment(*, create: bool = True) -> Any:
    """Acquire the selected sandbox; never substitute the native host."""
    backend = terminal_backend()
    if backend not in {"docker", "ssh", "singularity"}:
        raise RuntimeError(f"terminal backend {backend!r} cannot host browser commands; configure an explicit outgoing CDP endpoint instead")
    from tools import terminal_tool as terminal
    task_id = terminal._resolve_container_task_id(None)
    with terminal._env_lock:
        env = terminal._lookup_active_env(task_id, None)
    if env is None and create:
        terminal.terminal_tool("true", task_id=None)
        with terminal._env_lock:
            env = terminal._lookup_active_env(task_id, None)
    if env is None:
        raise RuntimeError(f"the selected {backend} terminal environment is not running")
    return env


def browser_profile_dir(env: Any) -> str:
    """Keep the remote browser's generated profile in its selected user's persistent home."""
    from tools.environments import streams
    from hermes_constants import get_hermes_home
    home = getattr(env, "_hermes_browser_home", None)
    if home is None:
        probe = streams.run_in(env, ["sh", "-c", 'printf %s "$HOME"'], timeout=15)
        home = probe.stdout.decode("utf-8", "replace").strip() if probe.returncode == 0 else ""
        if not home.startswith("/"):
            raise RuntimeError("the selected terminal backend did not report its user's home")
        env._hermes_browser_home = home
    scope = hashlib.sha256(str(get_hermes_home().resolve()).encode()).hexdigest()[:16]
    return f"{home.rstrip('/')}/.cache/hermes-agent/browser/{scope}"


def require_browser_cli(env: Any) -> None:
    """The selected sandbox owns browser prerequisites; never borrow the host CLI."""
    from tools.environments import streams

    probe = streams.run_in(env, ["sh", "-c", "command -v agent-browser >/dev/null"], timeout=15)
    if probe.returncode != 0:
        raise RuntimeError(
            "agent-browser is missing inside the selected sandbox. Install agent-browser and Chromium "
            "in that image (docker/sandbox-terminal.Dockerfile provides the pinned stack), "
            "or configure an explicit outgoing CDP endpoint with a sandbox-local agent-browser CLI."
        )


def require_native_computer_use() -> None:
    backend = terminal_backend()
    if backend != "local":
        raise RuntimeError(f"native computer use is unavailable with terminal backend {backend!r}; refusing to control the host outside the selected sandbox")
