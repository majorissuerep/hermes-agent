"""Local coding-agent commands under the selected encrypted profile's authority."""
from __future__ import annotations

import contextlib
from pathlib import Path
import os
from typing import Iterator
import uuid

from hermes_cli.management_config import ManagementError

RUNNERS = {
    "hermes": {"command": "hermes", "arguments": [], "persistence": "encrypted Hermes history"},
    "codex": {"command": "codex", "arguments": ["exec", "--ephemeral", "-c", "shell_environment_policy.inherit=all",
              "-c", "features.shell_snapshot=false", "-c", "features.shell_snapshot_v2=false",
              "-c", "shell_environment_policy.ignore_default_excludes=false",
              "-c", "history.persistence=none", "-c", "memories.generate_memories=false"], "persistence": "disabled"},
    "claude": {"command": "claude", "arguments": ["--print", "--no-session-persistence"], "persistence": "disabled"},
}
_VENDOR_ENV = {
    "codex": (("OPENAI_API_KEY", "CODEX_API_KEY"), ("OPENAI_API_BASE", "OPENAI_BASE_URL"),
              ("OPENAI_BASE_URL", "OPENAI_BASE_URL")),
    "claude": (("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"), ("ANTHROPIC_BASE_URL", "ANTHROPIC_BASE_URL")),
}
# Argument policy, checked against codex-cli 0.166.0 and Claude Code 2.1.316 help.
# Invocation is one-shot: resume, fork, background, cloud or desktop sessions
# would escape this owner's lifetime.
_LIFECYCLE = {
    "codex": frozenset({"resume", "fork"}),
    "claude": frozenset({"--resume", "-r", "--continue", "-c", "--background", "--bg", "--remote-control",
                         "--fork-session", "--from-pr", "--teleport", "--cloud", "--environment", "--desktop"}),
}
# Flags that skip approvals, the sandbox, hook trust or execpolicy rules, or widen the
# writable/accessible roots. `--yolo` is codex's hidden alias of the sandbox bypass; codex
# exec takes no `-a`, and `--approve-for-me` hands escalation approval to automatic review.
_RELAXING = {
    "codex": frozenset({"--dangerously-bypass-approvals-and-sandbox", "--yolo", "--dangerously-bypass-hook-trust",
                        "--approve-for-me", "--ignore-rules", "--add-dir", "-a", "--ask-for-approval"}),
    # A `--settings` file or JSON can carry `permissions.defaultMode`; its content is not inspected.
    "claude": frozenset({"--dangerously-skip-permissions", "--allow-dangerously-skip-permissions", "--add-dir",
                         "--settings"}),
}
# Short options that take the rest of a clustered token as their value (`-sVALUE`, `-pc` = `-p -c`).
_SHORT_VALUES = {"codex": frozenset("cimpsCo"), "claude": frozenset("dnrw")}
_VALUE_OPTIONS = {
    "codex": {"-s": "sandbox", "--sandbox": "sandbox", "-c": "config", "--config": "config",
              "--enable": "feature", "--disable": "feature"},
    "claude": {"--permission-mode": "permission mode"},
}
# Allow-lists, so a future relaxed mode is refused. Claude's bypassPermissions skips every
# check and acceptEdits/auto approve edits or actions without a person; dontAsk only
# denies what is not pre-approved, plan is read-only, manual/default ask.
_SAFE_VALUES = {
    "sandbox": frozenset({"read-only", "workspace-write"}),
    "permission mode": frozenset({"default", "manual", "plan", "dontask"}),
}
# Codex config the runner pins, or that relaxes sandbox/approvals/trust, or that defines
# commands codex launches outside its own sandbox. Setting a path, a child of it, or a
# parent table (`features={...}`) is refused.
_CODEX_PROTECTED = tuple(tuple(path.split(".")) for path in (
    "sandbox_mode", "sandbox_workspace_write", "sandbox_permissions", "permissions", "default_permissions",
    "approval_policy", "ask_for_approval", "approvals_reviewer", "profiles", "projects",
    "features.shell_snapshot*", "history", "memories", "shell_environment_policy",
    "mcp_servers", "hooks", "notify",
))


def _flag_names(arg: str, valued: frozenset[str]) -> list[str]:
    """Option names one token spells, expanding clustered short flags."""
    if arg.startswith("--") or not arg.startswith("-"):
        return [arg.split("=", 1)[0]]
    names = []
    for char in arg[1:]:
        names.append("-" + char)
        if char in valued or char == "=":
            break
    return names


def _option_values(arguments: list[str], options: dict[str, str]) -> Iterator[tuple[str, str]]:
    """(kind, value) for `--o v`, `--o=v`, `-o v`, `-o=v` and `-ov` spellings."""
    for index, arg in enumerate(arguments):
        following = arguments[index + 1] if index + 1 < len(arguments) else ""
        for spelling, kind in options.items():
            if arg == spelling:
                yield kind, following
            elif arg.startswith(spelling + "="):
                yield kind, arg[len(spelling) + 1:]
            elif not spelling.startswith("--") and not arg.startswith("--") and arg.startswith(spelling):
                yield kind, arg[len(spelling):]


def _codex_protected(key: str) -> bool:
    from fnmatch import fnmatchcase
    path = [part.strip().strip("\"'").strip().lower().replace("-", "_") for part in key.split(".")]
    # zip() compares the shared prefix: the path itself, anything below it, or a parent table.
    return any(all(fnmatchcase(part, pattern) for part, pattern in zip(path, protected))
               for protected in _CODEX_PROTECTED)


def _refuse_value(kind: str, value: str) -> None:
    if kind in _SAFE_VALUES:
        if value.strip().strip("\"'").lower() not in _SAFE_VALUES[kind]:
            raise ManagementError("invalid_request", f"managed runners refuse {kind} {value!r}; "
                                  f"allowed: {', '.join(sorted(_SAFE_VALUES[kind]))}")
        return
    key, separator, _ = ("features." + value, "=", "") if kind == "feature" else value.partition("=")
    if not separator or _codex_protected(key):
        raise ManagementError("invalid_request", f"managed runners own the codex setting {key.strip()!r} "
                              "(sandbox, approvals, persistence, shell environment, launched commands)")


def _refuse_relaxation(runner: str, arguments: list[str]) -> None:
    """Refuse lifecycle escapes and every supported permission or sandbox relaxation."""
    names = {name for arg in arguments for name in _flag_names(arg, _SHORT_VALUES[runner])}
    if names & _LIFECYCLE[runner]:
        raise ManagementError("invalid_request", "managed runners require a new foreground one-shot task")
    if relaxed := sorted(names & _RELAXING[runner]):
        raise ManagementError("invalid_request", f"managed runners keep {runner}'s permission and sandbox "
                              f"policy; {relaxed[0]} is refused")
    for kind, value in _option_values(arguments, _VALUE_OPTIONS[runner]):
        _refuse_value(kind, value)


def _command(runner: str, arguments: list[str]) -> list[str]:
    if runner == "hermes":
        from hermes_cli._launchers import runtime_command
        return runtime_command(Path(__file__).resolve().parents[1], arguments)
    from hermes_platform.resolver import locate_command
    from hermes_platform.resolver.known_dirs import node_tool_dirs, user_local_bin
    resolved = locate_command(RUNNERS[runner]["command"], known_dirs=(*user_local_bin(), *node_tool_dirs())).command
    if not resolved:
        raise ManagementError("unavailable", f"{runner} is not installed; install and authenticate its CLI first")
    if "--" in arguments:
        raise ManagementError("invalid_request", "pass runner arguments after one outer --; nested option terminators are unsupported")
    _refuse_relaxation(runner, arguments)
    defaults = RUNNERS[runner]["arguments"]
    return ([*resolved, defaults[0], *arguments, *defaults[1:]] if runner == "codex"
            else [*resolved, *defaults, *arguments])


_SOURCE = Path(__file__).resolve().parents[1]
_TEMP_NAMES = ("TMPDIR", "TMP", "TEMP", "XDG_RUNTIME_DIR")


def _borrowed_grant_only(home: Path) -> bool:
    """An operation grant without a local key belongs to an owner elsewhere."""
    from hermes_cli.vault_gate import _owning_vault_home
    from hermes_security.session_authority import ENV_NAME
    from hermes_security.vault import is_unlocked
    if not os.environ.get(ENV_NAME) or "HERMES_VAULT_KEY_FD" in os.environ:
        return False
    owner = _owning_vault_home(home)
    return owner is not None and not is_unlocked(owner)


def _forward(runner: str, arguments: list[str]) -> int:
    """The owning session runs the same fixed command natively; this process holds no key."""
    from hermes_cli.session_proxy import main
    return main(["manage", "run", runner, "--", *arguments])


@contextlib.contextmanager
def _vendor_scratch(runner: str) -> Iterator[Path | None]:
    """Vendor temp files may hold the credentials in its environment: RAM-only, per task."""
    if runner == "hermes":
        yield None
        return
    try:
        from hermes_security.protected_workspace import ProtectedWorkspaceUnavailable, protected_workspace
    except ImportError:
        raise ManagementError("unavailable", "protected vendor scratch is unavailable in this installation") from None
    # The canonical base (security.protected_work_dir, else XDG_RUNTIME_DIR) is only a
    # candidate; the workspace proves its backing itself and never falls back.
    try:
        with protected_workspace("managed-" + runner) as scratch:
            yield scratch
    except ProtectedWorkspaceUnavailable as exc:
        raise ManagementError("unavailable", "managed vendor runs need a private scratch proven to be RAM that is "
                              f"never swapped; refusing: {exc}") from None


def _vendor_env(env: dict[str, str], home: Path, runner: str) -> None:
    from agent.secret_scope import build_profile_secret_scope
    scope = build_profile_secret_scope(home)
    bindings = _VENDOR_ENV[runner]
    # A profile endpoint travels with its key; a CLI-owned login
    # keeps its own routing when the profile has no vendor key.
    if scope.get(bindings[0][0]):
        for source, destination in bindings:
            if value := scope.get(source):
                env[destination] = value


def _child_env(home: Path, runner: str, directory: Path) -> dict[str, str]:
    from tools.environments.local import served_profile_child_env
    env = served_profile_child_env(target_home=home)
    env["PATH"] = str(directory) + os.pathsep + env.get("PATH", "")
    env["HERMES_MANAGEMENT_BIN"] = str(directory)
    shell_init = str(_SOURCE / "scripts" / "managed-agent-shell.sh")
    if env.get("BASH_ENV") != shell_init:
        env["HERMES_PREVIOUS_BASH_ENV"] = env.get("BASH_ENV", "")
    env["BASH_ENV"] = shell_init
    for name in ("HERMES_VAULT_KEY_FD", "HERMES_VAULT_PRIVATE_KEY", "HERMES_MASTER_PASSWORD",
                 "CODEX_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(name, None)
    if runner != "hermes":
        _vendor_env(env, home, runner)
    return env


def _route_reads(command: list[str], directory: Path) -> list[str]:
    """Read-only paths the managed route itself needs; never a vendor's own login store."""
    import sys
    executable = Path(os.path.realpath(command[0]))
    installed = executable.parent.parent if executable.parent.name == "bin" else executable
    from hermes_cli.runtime_launcher import operation_client_paths
    interpreter = Path(os.path.realpath(sys.executable))
    paths = {str(installed), str(directory), str(interpreter.parent.parent), sys.prefix, sys.base_prefix,
             sys.exec_prefix, *operation_client_paths(_SOURCE)}
    return sorted(path for path in paths if path and path != "/")


def _confine(command: list[str], env: dict[str, str], directory: Path,
             pipe: Path, scratch: Path | None) -> list[str]:
    """The terminal sandbox confines the executor; an unenforceable sandbox refuses."""
    from hermes_security.sandbox.spawn import SandboxUnavailable, apply_env, sandboxed_spawn
    try:
        command, overrides = sandboxed_spawn(command, extra_read=_route_reads(command, directory),
                                             extra_write=[str(pipe), *([str(scratch)] if scratch else [])])
    except SandboxUnavailable as exc:
        raise ManagementError("unavailable", f"{exc}; refusing to run the executor unconfined") from None
    apply_env(env, overrides)
    if scratch is not None:
        env.update(dict.fromkeys(_TEMP_NAMES, str(scratch)))
    return command


def _trusted_key(home: Path, env: dict[str, str]) -> int:
    """Only the trusted Hermes child receives the one-shot key pipe."""
    from hermes_security.handoff import prepare_child_key
    handoff = prepare_child_key(home)
    if handoff is None:
        raise ManagementError("locked", "the selected profile is not unlocked")
    env.update(handoff[1])
    return handoff[0]


def _launch(command: list[str], env: dict[str, str], inherit_fds: tuple[int, ...],
            *, scratch: Path | None = None) -> int:
    """Owner exit or cancel ends the executor's whole tree; the caller closes ``inherit_fds``."""
    from hermes_platform.managed_supervision import run_foreground
    return run_foreground(command, env=env, inherit_fds=inherit_fds,
                          cleanup_dirs=() if scratch is None else (scratch,))


def run(home: Path, runner: str, arguments: list[str]) -> int:
    """Reuse an unlock; never select ambient Hermes or relax an executor's permissions."""
    if runner not in RUNNERS:
        raise ManagementError("invalid_request", "runner must be hermes, codex or claude")
    from hermes_cli.management import _authorize, _safe_target
    _safe_target(home)
    if _borrowed_grant_only(home):
        return _forward(runner, arguments)
    _authorize(home)
    command = _command(runner, arguments)
    from agent.session_context import scoped_current_session_id
    from hermes_cli.runtime_launcher import private_cli
    from hermes_security.session_authority import start_session_authority
    authority = start_session_authority(home, "managed-" + uuid.uuid4().hex, pipe=True)
    if authority is None:
        raise ManagementError("locked", "the selected profile is not unlocked")
    key = None
    try:
        with (scoped_current_session_id(authority.session_id), private_cli(_SOURCE, operation_client=runner != "hermes") as directory,
              _vendor_scratch(runner) as scratch):
            env = _child_env(home, runner, directory)
            if runner == "codex":
                from hermes_cli.managed_agent_routes import codex_api_arguments
                command.extend(codex_api_arguments(env))
                command.extend(["--add-dir", str(authority.server.path)])
            if runner == "hermes":
                # Hermes itself confines its own tool spawns under the same policy.
                key = _trusted_key(home, env)
            else:
                command = _confine(command, env, directory, authority.server.path, scratch)
            return _launch(command, env, () if key is None else (key,), scratch=scratch)
    finally:
        if key is not None:
            os.close(key)
        authority.close()
