"""Local JSON management boundary. No listener, model tool, or plaintext state fallback."""
from __future__ import annotations

import argparse
import contextlib
import json
import io
import os
import select
import stat
import sys
import time

from hermes_cli.management_config import ManagementError

OPERATIONS = {
    "config.get": ["path"], "config.set": ["path", "value", "revision"],
    "config.unset": ["path", "revision"], "config.batch": ["operations", "revision"],
    "secret.set": ["name", "secret_fd"], "secret.unset": ["name"],
    "passphrase.change": ["secret_fd"], "status": [],
}
SECRET_READ_DEADLINE_SECONDS = 5.0


def schema():
    return {"version": 1, "operations": OPERATIONS, "path": "array of string keys or integer list indices",
            "secrets": "inherited one-shot secret_fd >= 3, distinct from unlock FD; UTF-8, no trailing newline",
            "secret_read_deadline_seconds": SECRET_READ_DEADLINE_SECONDS,
            "config_batch": "atomic config-only; no cross-file transactions", "effect": "next_session",
            "errors": ["invalid_request", "invalid_operation", "invalid_path", "not_found", "invalid_type", "invalid_value",
                       "invalid_config", "conflict", "secret_operation_required", "secret_timeout", "locked", "state_refused"],
            "lifecycle_commands": ["hermes profile --help", "hermes sessions --help", "hermes skills --help",
                                   "hermes mcp --help", "hermes plugins --help", "hermes cron --help",
                                   "hermes backup --help", "hermes import --help", "hermes secure-vault --help", "hermes update --help"]}


def _request(value):
    if not isinstance(value, dict) or not isinstance(value.get("op"), str):
        raise ManagementError("invalid_request", "request must be an object with an op")
    if value["op"] not in OPERATIONS:
        raise ManagementError("invalid_operation", "operation is not supported; see manage schema")
    if set(value) - {"op", *OPERATIONS[value["op"]]}:
        raise ManagementError("invalid_request", "request has unsupported fields")
    return value


def _secret(request):
    fd = request.get("secret_fd")
    if type(fd) is not int or fd < 3 or str(fd) == os.environ.get("HERMES_VAULT_KEY_FD"):
        raise ManagementError("invalid_request", "secret_fd must be distinct from stdin and unlock authority")
    chunks, size = [], 0
    kind = os.fstat(fd).st_mode
    if not (stat.S_ISFIFO(kind) or stat.S_ISSOCK(kind) or stat.S_ISREG(kind)):
        raise ManagementError("invalid_request", "secret_fd must be a one-shot pipe or file")
    deadline = time.monotonic() + SECRET_READ_DEADLINE_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], max(0, remaining))[0]:
            raise ManagementError("secret_timeout", "secret input did not reach EOF before its deadline")
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        size += len(chunk)
        if size > 65536:
            raise ManagementError("invalid_request", "secret input is too large")
        chunks.append(chunk)
    value = b"".join(chunks).decode("utf-8")
    if not value or "\0" in value or "\n" in value or "\r" in value:
        raise ManagementError("invalid_request", "secret input must be one nonempty UTF-8 value without newlines")
    return value


def _authorize(home):
    from hermes_cli.vault_gate import _owning_vault_home
    from hermes_security import vault
    from hermes_security.handoff import adopt_inherited_key
    owner = _owning_vault_home(home)
    if owner is None:
        raise ManagementError("locked", "initialize and unlock the selected home before management")
    # The canonical handoff handles either one-shot FD or the current scoped session grant.
    adopt_inherited_key()
    if not vault.is_unlocked(owner):
        if os.environ.get("HERMES_VAULT_PRIVATE_KEY"):
            vault.get_vault(owner)  # canonical key-slot proof + owner-only key-file policy
        else:
            from hermes_cli.vault_cmd import _password_from_env_or_prompt
            try:
                password = _password_from_env_or_prompt()
            except SystemExit:
                raise ManagementError("locked", "unlock the selected home before management") from None
            vault.unlock(owner, password)
    vault.get_vault(owner, allow_env_unlock=False)


def _safe_target(home):
    for path in (home, home / "config.yaml", home / ".env", home / "config.yaml.lock", home / ".env.lock"):
        if path.is_symlink():
            raise ManagementError("state_refused", "management refuses symbolic state paths")


def _execute(request, home):
    request = _request(request)
    from hermes_constants import get_hermes_home
    if home.resolve() != get_hermes_home().resolve():
        raise ManagementError("state_refused", "management target does not match the selected owning home")
    _safe_target(home)
    _authorize(home)
    op = request["op"]
    if op.startswith("config."):
        from hermes_cli.management_config import config_operation
        return config_operation(home, request)
    if op == "status":
        from hermes_cli.management_config import config_operation
        config_operation(home, {"op": "config.get"})
        from hermes_cli.config import load_env
        return {"home": str(home), "unlocked": True, "secret_names": sorted(load_env()), "effect": "next_session"}
    if op == "passphrase.change":
        from hermes_security.passphrase import change_passphrase
        change_passphrase(home, _secret(request))
        return {"changed": True, "effect": "future_unlocks"}
    from hermes_cli.management_secrets import secret_operation
    return secret_operation(request, _secret(request) if op == "secret.set" else None)


def execute(request, home):
    """One-shot resource custody, including refusal paths; requires owning unlock authority."""
    fd = request.get("secret_fd") if isinstance(request, dict) else None
    unlock_fd = os.environ.get("HERMES_VAULT_KEY_FD")
    if fd is not None:
        try:
            inherited = type(fd) is int and fd >= 3 and os.get_inheritable(fd)
        except OSError:
            inherited = False
        if not inherited or str(fd) == unlock_fd:
            raise ManagementError("invalid_request", "secret_fd must be inherited and distinct from unlock authority")
    try:
        return _execute(request, home)
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ManagementError("invalid_request", "invalid command arguments; use manage schema")


@contextlib.contextmanager
def _home_scope(profile):
    from hermes_constants import get_hermes_home, reset_hermes_home_override, set_hermes_home_override
    from agent.secret_scope import reset_secret_scope, set_secret_scope
    home = get_hermes_home()
    if profile is None and home.parent.name != "profiles":
        from hermes_cli.profiles import get_active_profile
        profile = get_active_profile(home)
    if profile is not None:
        from hermes_cli.profiles import resolve_profile_env
        from pathlib import Path
        home = Path(resolve_profile_env(profile))
    token = set_hermes_home_override(home)
    secrets = set_secret_scope({}, profile_home=str(home))
    try:
        yield home
    finally:
        reset_secret_scope(secrets)
        reset_hermes_home_override(token)


def main(argv=None):
    parser = _Parser(prog="hermes manage", description="Local structured management; secrets use inherited FDs.")
    parser.add_argument("--profile", help="existing profile, otherwise the selected owning home")
    parser.add_argument("command", choices=("schema", "apply"), nargs="?", default="schema")
    try:
        args = parser.parse_args(argv)
        if args.command == "schema":
            result = schema()
        else:
            raw = sys.stdin.read(1048577)
            if len(raw) > 1048576:
                raise ManagementError("invalid_request", "request is too large")
            request = json.loads(raw)
            with _home_scope(args.profile) as home:
                with contextlib.redirect_stdout(io.StringIO()):
                    result = execute(request, home)
        response, rc = {"version": 1, "ok": True, "result": result}, 0
        json.dumps(response, allow_nan=False)
    except ManagementError as exc:
        response, rc = {"version": 1, "ok": False, "error": {"code": exc.code, "message": str(exc)}}, 2
    except json.JSONDecodeError:
        response, rc = {"version": 1, "ok": False, "error": {"code": "invalid_request", "message": "stdin must contain one valid JSON request"}}, 2
    except (OSError, ValueError, TypeError, RuntimeError):
        # Parser/provider/crypto exceptions may contain rejected secrets or raw YAML. Never echo them.
        response, rc = {"version": 1, "ok": False, "error": {"code": "state_refused", "message": "operation refused; check the selected home and unlock authority"}}, 2
    print(json.dumps(response, ensure_ascii=False, allow_nan=False))
    return rc
