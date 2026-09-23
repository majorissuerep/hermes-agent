"""User-native shell resolution: the OS user's OWN login shell, not Hermes' bash.

The terminal tool historically ran every command under ``bash`` — a fork of
the upstream behavior this module replaces.  A zsh/fish/dash user's commands
must run under THEIR shell with THEIR syntax, and children (vim ``:!``, ssh,
tmux) must see ``$SHELL`` pointing at it, not ``/bin/sh``.

Resolution order (first hit wins):
1. ``pwd.getpwuid(os.getuid()).pw_shell`` — the account's login shell; the
   authoritative source even when ``$SHELL`` is missing or stale (systemd
   units, cron, non-login SSH, GUI launchers).
2. ``$SHELL`` when it names an existing executable.
3. bash fallback (the historical behavior) — the machinery still needs a
   POSIX shell to source snapshots.

A shell is accepted only if the binary exists and is executable; NIS/LDAP
entries can name shells absent on this host.
"""

from __future__ import annotations

import os
import pwd
import shutil
from pathlib import Path

# Shells we can hand a ``-c`` command to.  Excludes login-only managers that
# cannot take -c usefully; those fall back to bash.
_SHELL_CAPABLE_SUFFIX_OK = True

# Login managers / shells that cannot execute ``-c`` commands.
_NOT_COMMAND_SHELLS = frozenset({
    "nologin", "false", "sync", "halt", "shutdown", "git-shell",
})


def resolve_user_shell() -> str:
    """The user's native login shell binary path (bash fallback guaranteed)."""

    candidates: list[str] = []
    try:
        entry = pwd.getpwuid(os.getuid())
        if entry.pw_shell:
            candidates.append(entry.pw_shell)
    except (KeyError, OSError):
        pass
    env_shell = os.environ.get("SHELL", "")
    if env_shell:
        candidates.append(env_shell)
    for shell in candidates:
        if _usable_command_shell(shell):
            return shell
    return _bash_fallback()


def _usable_command_shell(shell: str) -> bool:
    if not shell or not os.path.isabs(shell):
        return False
    name = Path(shell).name
    if name in _NOT_COMMAND_SHELLS:
        return False
    return os.path.isfile(shell) and os.access(shell, os.X_OK)


def _bash_fallback() -> str:
    return (shutil.which("bash")
            or next((p for p in ("/usr/bin/bash", "/bin/bash") if os.path.isfile(p)), None)
            or os.environ.get("SHELL")
            or "/bin/sh")


def is_posix_shell_family(shell: str) -> bool:
    """True when the shell speaks POSIX ``sh`` syntax (bash/zsh/dash/ksh...).

    fish, nushell, powershell and friends need translation or a different
    invocation shape; callers route those through a wrapper.
    """

    name = Path(shell or "").name.lower()
    if name in _NOT_COMMAND_SHELLS:
        return False
    return name in {"bash", "zsh", "sh", "dash", "ash", "ksh", "mksh", "posh", "yash"}


def ensure_shell_env_var(env: dict) -> None:
    """Set ``SHELL`` in *env* to the user's native shell (never overwrite a
    caller's explicit value; Hermes vars are an overlay, not a blindfold)."""

    if env.get("SHELL"):
        return
    shell = resolve_user_shell()
    if shell:
        env["SHELL"] = shell
