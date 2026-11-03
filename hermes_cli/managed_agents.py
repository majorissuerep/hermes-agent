"""Local coding-agent commands under the selected encrypted profile's authority."""
from __future__ import annotations

from pathlib import Path
import os
import subprocess
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
    # Invocation is one-shot. A resume/background mode would escape this owner's lifetime.
    forbidden = {"resume", "fork"} if runner == "codex" else {
        "--resume", "-r", "--continue", "-c", "--background", "--bg", "--remote-control"}
    if any(arg.split("=", 1)[0] in forbidden for arg in arguments):
        raise ManagementError("invalid_request", "managed runners require a new foreground one-shot task")
    if runner == "codex" and any(
        (arg.startswith("--enable=shell_snapshot") or
         (arg == "--enable" and index + 1 < len(arguments) and arguments[index + 1].startswith("shell_snapshot")))
        for index, arg in enumerate(arguments)
    ):
        raise ManagementError("invalid_request", "managed runners keep credential-bearing shell snapshots disabled")
    defaults = RUNNERS[runner]["arguments"]
    return ([*resolved, defaults[0], *arguments, *defaults[1:]] if runner == "codex"
            else [*resolved, *defaults, *arguments])


def run(home: Path, runner: str, arguments: list[str]) -> int:
    """Reuse an unlock; never select ambient Hermes or relax an executor's permissions."""
    if runner not in RUNNERS:
        raise ManagementError("invalid_request", "runner must be hermes, codex or claude")
    from hermes_cli.management import _authorize, _safe_target
    _safe_target(home)
    _authorize(home)
    from agent.session_context import scoped_current_session_id
    from hermes_security.session_authority import start_session_authority
    from tools.environments.local import served_profile_child_env
    authority = start_session_authority(home, "managed-" + uuid.uuid4().hex, pipe=True)
    if authority is None:
        raise ManagementError("locked", "the selected profile is not unlocked")
    try:
        with scoped_current_session_id(authority.session_id):
            env = served_profile_child_env(target_home=home)
            from hermes_cli.runtime_launcher import write_cli
            directory = write_cli(authority.server.path, Path(__file__).resolve().parents[1])
            env["PATH"] = str(directory) + os.pathsep + env.get("PATH", "")
            env["HERMES_MANAGEMENT_BIN"] = str(directory)
            shell_init = str(Path(__file__).resolve().parents[1] / "scripts" / "managed-agent-shell.sh")
            if env.get("BASH_ENV") != shell_init:
                env["HERMES_PREVIOUS_BASH_ENV"] = env.get("BASH_ENV", "")
            env["BASH_ENV"] = shell_init
            env.pop("HERMES_VAULT_KEY_FD", None)
            env.pop("HERMES_VAULT_PRIVATE_KEY", None)
            env.pop("HERMES_MASTER_PASSWORD", None)
            for name in ("CODEX_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN"):
                env.pop(name, None)
            if runner != "hermes":
                from agent.secret_scope import build_profile_secret_scope
                scope = build_profile_secret_scope(home)
                bindings = _VENDOR_ENV[runner]
                # A profile endpoint travels with its key; a CLI-owned login
                # keeps its own routing when the profile has no vendor key.
                if scope.get(bindings[0][0]):
                    for source, destination in bindings:
                        if value := scope.get(source):
                            env[destination] = value
            command = _command(runner, arguments)
            if runner == "codex":
                command.extend(["--add-dir", str(authority.server.path)])
            # The existing terminal policy still confines a managed external executor.
            from hermes_security.sandbox.spawn import apply_env, sandboxed_spawn
            command, overrides = sandboxed_spawn(command)
            apply_env(env, overrides)
            result = subprocess.run(command, env=env, check=False)  # health: allow HX006 -- foreground task lifetime and cancellation are owned by the invoking terminal
            return result.returncode if result.returncode >= 0 else 128 - result.returncode
    finally:
        authority.close()
