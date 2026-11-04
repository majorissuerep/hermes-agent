"""Operation grants supervise fixed native Hermes commands without exporting keys."""
from __future__ import annotations

import base64
import os
from pathlib import Path
import secrets
import threading
import time

_lock = threading.RLock()
_jobs = {}
_COMMANDS = {"chat", "manage", "doctor", "config", "sessions", "memory", "skills", "tools", "mcp", "cron",
             "kanban", "profile", "auth", "help", "status", "backup", "import"}
_EXECUTION_FLAGS = {"--run-module", "--code", "--python", "--script", "--monitor-script", "--workdir", "--cwd",
                    "--in", "--worktree", "-w", "--accept-hooks", "--yolo"}


def _scope_arguments(command_args, arguments):
    """Vendor arguments retain their CLI meaning; nested Hermes retains this boundary."""
    if command_args[:2] != ["manage", "run"]:
        return arguments
    if len(command_args) < 3 or command_args[2] not in {"hermes", "codex", "claude"}:
        raise ValueError("the session grant requires a supported managed runner")
    child = command_args[3:]
    if child[:1] == ["--"]:
        child = child[1:]
    if command_args[2] == "hermes":
        checked_arguments(child)
    return arguments[:len(arguments) - len(child)] if child else arguments


def checked_arguments(arguments):
    if (not isinstance(arguments, list) or len(arguments) > 256
            or any(not isinstance(arg, str) or "\0" in arg or len(arg) > 65536 for arg in arguments)):
        raise ValueError("native arguments must be a bounded string array")
    if not arguments:
        raise ValueError("interactive Hermes requires the owning terminal; use hermes chat -q for a headless task")
    from hermes_cli._parser import command_argv
    query = any(arg.split("=", 1)[0] in {"-q", "--query", "--query-file", "-z", "--oneshot"} for arg in arguments)
    readonly = any(arg in {"--help", "-h", "--version", "-V"} for arg in arguments)
    command_args = command_argv(arguments)
    command = command_args[0] if command_args else ("chat" if query else "help" if readonly else "")
    if command not in _COMMANDS:
        raise ValueError("the session grant does not authorize this native command")
    scoped = _scope_arguments(command_args, arguments)
    if any(arg in {"-p", "--profile"} or arg.startswith("--profile=") for arg in scoped):
        raise ValueError("a borrowed command cannot select another profile")
    if any(arg.split("=", 1)[0] in _EXECUTION_FLAGS for arg in scoped):
        raise ValueError("a borrowed command cannot select code, an interpreter or a working directory")
    if command == "chat" and not (query or readonly):
        raise ValueError("interactive Hermes requires the owning terminal; use hermes chat -q for a headless task")
    return arguments


def profile_arguments(home: Path, arguments):
    """Resolve an explicit prefix from its owning home, never process-wide env."""
    if not isinstance(arguments, list) or not arguments:
        return arguments
    if len(arguments) >= 2 and arguments[0] in {"-p", "--profile"}:
        profile, arguments = arguments[1], arguments[2:]
    elif isinstance(arguments[0], str) and arguments[0].startswith("--profile="):
        profile, arguments = arguments[0].split("=", 1)[1], arguments[1:]
    else:
        return arguments
    from hermes_cli.profiles import normalize_profile_name, validate_profile_name, profile_root_for_env_home
    from hermes_constants import named_profile_is_live
    canon = normalize_profile_name(profile)
    validate_profile_name(canon)
    root = profile_root_for_env_home(str(home), home)
    selected = root if canon == "default" else root / "profiles" / canon
    if selected.resolve() != home or canon != "default" and not named_profile_is_live(selected):
        raise ValueError("a borrowed command cannot select another profile")
    return arguments


def _start(authority, request):
    from hermes_security.handoff import prepare_child_key
    from hermes_cli._launchers import runtime_command
    from tools.environments.local import served_profile_child_env
    from hermes_platform.authority_children import NativeChild
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from agent.secret_scope import set_secret_scope, reset_secret_scope
    from agent.session_context import scoped_current_session_id
    from hermes_security.session_authority import ENV_NAME

    arguments = checked_arguments(profile_arguments(authority.home, request.get("arguments")))
    data = base64.b64decode(request.get("stdin", ""), validate=True)
    if len(data) > 524288:
        raise ValueError("native stdin exceeds the bounded input limit")
    with _lock:
        # close_native takes this same lock after revocation. A previously dispatched request
        # cannot enter after that fence; an admitted child is registered before close can reap it.
        if not authority.is_live():
            raise ValueError("the owning session ended before the native command could start")
        for identity, (owner, child) in list(_jobs.items()):
            if owner is authority and child.finished.is_set() and time.monotonic() - child.last_poll >= 20:
                del _jobs[identity]
        if sum(owner is authority for owner, _ in _jobs.values()) >= 4:
            raise ValueError("the owning session already has four native commands")
        home_token = set_hermes_home_override(authority.home)
        secret_token = set_secret_scope({}, profile_home=str(authority.home))
        handoff = None
        try:
            with scoped_current_session_id(authority.session_id):
                env = served_profile_child_env(target_home=authority.home)
                grant_env = authority.environment()
                env.update(grant_env)
                env["HERMES_HOME"] = str(authority.home)
                env["HERMES_SESSION_ID"] = authority.session_id
                for name in ("HERMES_VAULT_KEY_FD", "HERMES_VAULT_PRIVATE_KEY", "HERMES_MASTER_PASSWORD"):
                    env.pop(name, None)
                handoff = prepare_child_key(authority.home, source_grant=grant_env[ENV_NAME])
                if handoff is None:
                    raise ValueError("the owning vault is locked")
                env.update(handoff[1])
                command = runtime_command(Path(__file__).resolve().parents[1], arguments)
                child = NativeChild(command, env=env, cwd=authority.cwd, key_fd=handoff[0], input_bytes=data)
                identity = secrets.token_hex(24)
                _jobs[identity] = (authority, child)
        finally:
            if handoff is not None:
                os.close(handoff[0])
            reset_secret_scope(secret_token)
            reset_hermes_home_override(home_token)
    return {"ok": True, "job": identity}


def dispatch_native(authority, request):
    try:
        if request["op"] == "native.start":
            return _start(authority, request)
        identity = request.get("job")
        with _lock:
            owner, child = _jobs.get(identity, (None, None)) if isinstance(identity, str) else (None, None)
        if owner is not authority:
            raise ValueError("the native command does not belong to this session")
        if request["op"] == "native.cancel":
            child.close()
            with _lock:
                _jobs.pop(identity, None)
            return {"ok": True}
        chunks, done, rc, reason = child.poll()
        if done:
            with _lock:
                _jobs.pop(identity, None)
        return {"ok": True, "chunks": [{"stream": stream, "data": base64.b64encode(data).decode("ascii")}
                                         for stream, data in chunks], "done": done, "returncode": rc, "reason": reason}
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        # Validation messages are fixed; provider/crypto exceptions may contain secrets.
        message = str(exc) if type(exc) is ValueError else "native command refused by its owning session"
        return {"ok": False, "native_error": message}


def close_native(authority):
    with _lock:
        children = [child for owner, child in _jobs.values() if owner is authority]
        for identity, (owner, _) in list(_jobs.items()):
            if owner is authority:
                del _jobs[identity]
    for child in children:
        child.close()
