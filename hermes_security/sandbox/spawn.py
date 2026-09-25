"""The one seam spawn sites call: wrap an argv in the session's OS sandbox.

``sandboxed_spawn(argv)`` returns ``(argv, env_overrides)``: unchanged argv and ``{}``
when the sandbox is disabled; otherwise the platform wrapper around argv plus the
private ``TMPDIR`` and ``None`` for the vault credentials a confined process must never
inherit (apply with :func:`apply_env`). It raises :class:`SandboxUnavailable` — never degrades to an
unconfined spawn — when the sandbox is enabled but the OS cannot enforce it.
"""

from __future__ import annotations

import functools
import os
import sys
from typing import Callable, Iterable, Mapping

# Captured ONCE at import and run with ``-c`` by the resolved base interpreter: a grant
# that makes the Hermes checkout or its venv writable must not let the model rewrite the
# launcher (or a venv shim) that the unconfined parent executes on the next spawn.
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "launcher.py"), encoding="utf-8") as _fh:
    _LAUNCHER_SOURCE = _fh.read()
_INTERPRETER = os.path.realpath(sys.executable)


class SandboxUnavailable(RuntimeError):
    """The sandbox is enabled but this host cannot enforce it."""


def _linux_available() -> tuple[bool, str]:
    from hermes_security.sandbox import launcher
    abi = launcher.landlock_abi()
    if abi < 1:
        return False, "Landlock is unavailable (needs Linux >= 5.13 with the landlock LSM enabled)"
    try:
        from hermes_platform.host.facts import process_arch
        launcher.seccomp_program({"amd64": "x86_64", "arm64": "aarch64"}.get(process_arch(), process_arch()),
                                 network=False, unix_sockets=False)
    except ValueError as exc:
        return False, str(exc)
    extras = []
    if abi < 4:
        extras.append("network on/off enforced by seccomp")
    if abi < 6:
        extras.append("no Landlock signal scoping (kernel < 6.12)")
    detail = f"Landlock ABI {abi} + seccomp" + (f" ({'; '.join(extras)})" if extras else "")
    return True, detail


def _linux_wrap(argv: list[str], spec: Mapping) -> list[str]:
    import json
    return [_INTERPRETER, "-I", "-S", "-c", _LAUNCHER_SOURCE,
            json.dumps(dict(spec), separators=(",", ":")), "--", *argv]


def _macos_available() -> tuple[bool, str]:
    from hermes_security.sandbox import seatbelt
    return seatbelt.available()


def _macos_wrap(argv: list[str], spec: Mapping) -> list[str]:
    from hermes_security.sandbox import seatbelt
    return seatbelt.wrap(argv, spec)


_BACKENDS: dict[str, tuple[str, Callable[[], tuple[bool, str]], Callable[[list[str], Mapping], list[str]]]] = {
    "linux": ("landlock", _linux_available, _linux_wrap),
    "darwin": ("seatbelt", _macos_available, _macos_wrap),
}


def _os_family() -> str:
    from hermes_platform.host.facts import os_family
    return os_family()


@functools.cache
def backend_status() -> dict:
    """``{"backend", "available", "detail"}`` for this host (kernel facts: cached per process)."""
    family = _os_family()
    entry = _BACKENDS.get(family)
    if entry is None:
        return {"backend": "none", "available": False,
                "detail": f"no OS sandbox backend for {family} (supported: Linux, macOS)"}
    name, probe, _ = entry
    ok, detail = probe()
    return {"backend": name, "available": ok, "detail": detail}


def require_backend() -> None:
    status = backend_status()
    if not status["available"]:
        raise SandboxUnavailable(f"sandbox is enabled but cannot be enforced here: {status['detail']}")


def wrap_argv(argv: list[str], spec: Mapping) -> list[str]:
    require_backend()
    return _BACKENDS[_os_family()][2](list(argv), spec)


def sandboxed_spawn(argv: list[str], *, extra_read: Iterable[str] = (),
                    extra_write: Iterable[str] = ()) -> tuple[list[str], dict[str, str]]:
    from hermes_security.sandbox.policy import is_enabled
    if not is_enabled():
        return list(argv), {}
    from agent.delegation_context import is_delegated_child_context
    from hermes_security.sandbox.session import effective_policy
    policy = effective_policy()
    spec = policy.launch_spec(subagent=is_delegated_child_context(),
                              extra_read=extra_read, extra_write=extra_write)
    env: dict[str, str | None] = {"TMPDIR": policy.tmp_dir, "TMP": policy.tmp_dir, "TEMP": policy.tmp_dir}
    env.update(dict.fromkeys(_VAULT_CREDENTIALS))
    return wrap_argv(argv, spec), env


# Vault unlock credentials to scrub from sandboxed children. The passphrase env var is no
# longer READ anywhere (fork policy: never a passphrase in the environment) but is still
# scrubbed here so a stale exported value on legacy hosts cannot leak into a sandbox child;
# HERMES_VAULT_PRIVATE_KEY carries only a PATH.
_VAULT_CREDENTIALS = ("HERMES_MASTER_PASSWORD", "HERMES_VAULT_PRIVATE_KEY")


def apply_env(env: dict, overrides: Mapping[str, str | None]) -> dict:
    """Apply ``sandboxed_spawn`` overrides in place: ``None`` removes the variable."""
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env
