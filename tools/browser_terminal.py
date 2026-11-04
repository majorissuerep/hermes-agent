"""Browser execution in the explicitly selected outgoing terminal backend."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
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


@dataclass(frozen=True)
class BrowserRuntime:
    """Backend paths of the sandbox browser's private state, all inside one proven RAM directory."""

    root: str
    profile: str
    captures: str
    sockets: str
    tmp: str

    def guarded(self, argv: list[str]) -> list[str]:
        """``argv`` preceded by a check that every runtime directory is still ours and not a link.

        A recreated container or rebooted target loses the RAM directory; the guard refuses
        (exit ``RUNTIME_MISSING_EXIT``) instead of letting the browser recreate it unproven."""
        return ["sh", "-c", _GUARD, "hermes-browser-guard",
                self.root, self.profile, self.captures, self.sockets, self.tmp, *argv]


_RUNTIME_PARTS = ("profile", "captures", "sockets", "tmp")
RUNTIME_MISSING_EXIT = 97
RUNTIME_MISSING_MESSAGE = "hermes: the protected browser runtime is missing"
_GUARD = ('u=$(id -u); for p in "$1" "$2" "$3" "$4" "$5"; do '
          'if [ -L "$p" ] || [ ! -d "$p" ] || [ "$(stat -c %u:%a "$p" 2>/dev/null)" != "$u:700" ]; then '
          f'echo "{RUNTIME_MISSING_MESSAGE}" >&2; exit {RUNTIME_MISSING_EXIT}; fi; done; shift 5; exec "$@"')


def backend_identity(env: Any) -> str:
    return f"{terminal_backend()} terminal backend ({type(env).__name__})"


def _profile_scope() -> str:
    from hermes_constants import get_hermes_home
    return hashlib.sha256(str(get_hermes_home().resolve()).encode()).hexdigest()[:16]


def browser_runtime(env: Any) -> BrowserRuntime:
    """This profile's sandbox browser profile/capture/socket/temp directories.

    They live in an owner-only directory that the selected backend proves is never-swapped RAM
    (``hermes_platform.remote_workspace``); the control host's protected_work_dir cannot
    attest a remote filesystem. Reused for the backend's lifetime so the browser profile keeps its
    continuity across sessions; nothing is written to the target's disk. Raises an actionable
    ``RuntimeError`` naming the selected backend when no such directory can be proven.
    """
    scope = _profile_scope()
    cache = getattr(env, "_hermes_browser_runtimes", None)
    if cache is None:
        cache = env._hermes_browser_runtimes = {}
    from hermes_platform import remote_workspace
    from tools.environments import streams

    def run(argv, *, timeout):
        return streams.run_in(env, argv, timeout=timeout)

    temp = env.get_temp_dir()
    try:
        if scope in cache:
            runtime = cache[scope]
            remote_workspace.validate(run, runtime.root, _RUNTIME_PARTS)
            return runtime
        found = remote_workspace.acquire(run, f"hermes-browser-{scope}", _RUNTIME_PARTS, candidates=(temp,))
    except remote_workspace.ProtectedTargetUnavailable as exc:
        raise RuntimeError(
            f"The {backend_identity(env)} has no proven RAM-only directory for private browser "
            f"profile, capture and temporary files ({exc}). Mount a ramfs, or a tmpfs with the noswap "
            f"option, at $XDG_RUNTIME_DIR or {temp} inside that backend, or run it where no swap is "
            "active, then retry. Hermes does not change mounts or swap settings."
        ) from exc
    runtime = cache[scope] = BrowserRuntime(found.root, **found.children)
    return runtime


def runtime_missing(returncode: int, stderr: str) -> bool:
    return returncode == RUNTIME_MISSING_EXIT and RUNTIME_MISSING_MESSAGE in (stderr or "")


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
