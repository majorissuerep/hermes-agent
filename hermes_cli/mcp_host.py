"""Noninteractive MCP operations in the already-unlocked session host.

The terminal child gets neither vault keys nor a plaintext config. Existing MCP
RPCs own validation, profile binding and encrypted persistence. Saving does not
reload live agents, so their prompt prefixes remain stable.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
import sys

HOST_ACTIONS = {
    "add": "mcp.servers.add",
    "list": "mcp.servers.list",
    "ls": "mcp.servers.list",
    "remove": "mcp.servers.remove",
    "rm": "mcp.servers.remove",
    "test": "mcp.servers.test",
    "set-api-key": "mcp.servers.set_api_key",
    "catalog": "mcp.catalog",
}
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_STDIN_LIMIT = 1024 * 1024


def is_host_mcp_command(args) -> bool:
    """Precise vault-gate exemption: these commands only send authenticated RPCs."""
    return (
        getattr(args, "command", None) == "mcp"
        and bool(getattr(args, "via_host", False))
        and getattr(args, "mcp_action", None) in HOST_ACTIONS
    )


def _read_stdin(label: str) -> str:
    if sys.stdin.isatty():
        raise ValueError(f"{label} requires piped stdin")
    value = sys.stdin.read(_STDIN_LIMIT + 1)
    if len(value) > _STDIN_LIMIT:
        raise ValueError(f"{label} exceeds the stdin size limit")
    return value


def _assignments(items, *, env: bool) -> dict[str, str]:
    parsed = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        key = key.strip()
        if not sep or not key or (env and not _ENV_NAME.fullmatch(key)):
            raise ValueError("Expected a valid NAME=VALUE assignment")
        parsed[key] = value
    return parsed


def _add_config(args) -> dict:
    cfg = {}
    raw = getattr(args, "config_json", None)
    if raw is not None:
        try:
            cfg = json.loads(_read_stdin("--config-json -") if raw == "-" else raw)
        except json.JSONDecodeError:
            raise ValueError("--config-json must contain valid JSON") from None
        if not isinstance(cfg, dict):
            raise ValueError("--config-json must be a server configuration object")
    for attr, key in (("url", "url"), ("mcp_command", "command"), ("auth", "auth")):
        if value := getattr(args, attr, None):
            cfg[key] = value
    cmd_args = getattr(args, "args", None) or []
    if cmd_args:
        cfg["args"] = cmd_args[1:] if cmd_args[0] == "--" else cmd_args
    for attr, key in (("env", "env"), ("header", "headers")):
        if items := getattr(args, attr, None):
            block = cfg.setdefault(key, {})
            if not isinstance(block, dict):
                raise ValueError(f"{key} must be an object")
            block.update(_assignments(items, env=key == "env"))
    if (timeout := getattr(args, "connect_timeout", None)) is not None:
        if not math.isfinite(timeout) or timeout < 1:
            raise ValueError(
                "--connect-timeout must be a finite number of at least 1 second"
            )
        cfg["connect_timeout"] = timeout
    if cfg.get("url") and cfg.get("command"):
        raise ValueError("Specify one transport: --url or --command")
    if cfg.get("url") and cfg.get("env"):
        raise ValueError("env is only supported for stdio servers")
    if (
        not cfg.get("url")
        and not cfg.get("command")
        and not getattr(args, "preset", None)
    ):
        raise ValueError("Specify --url, --command, --preset, or --config-json")
    for key in ("url", "command", "auth"):
        if key in cfg and not isinstance(cfg[key], str):
            raise ValueError(f"{key} must be a string")
    for key in ("env", "headers"):
        if key in cfg and (
            not isinstance(cfg[key], dict)
            or any(
                not isinstance(k, str) or not isinstance(v, str)
                for k, v in cfg[key].items()
            )
        ):
            raise ValueError(f"{key} must map names to strings")
    if "args" in cfg and (
        not isinstance(cfg["args"], list)
        or any(not isinstance(v, str) for v in cfg["args"])
    ):
        raise ValueError("args must be a list of strings")
    return cfg


def _params(args, profile: str) -> dict:
    action = args.mcp_action
    params = {"profile": profile}
    if getattr(args, "name", None):
        params["name"] = args.name
    if action == "add":
        params["config"] = _add_config(args)
        if preset := getattr(args, "preset", None):
            params["preset"] = preset
    if action == "set-api-key":
        value = _read_stdin("--value-stdin").rstrip("\r\n")
        if not value or "\n" in value or "\r" in value:
            raise ValueError("--value-stdin requires one nonempty credential line")
        params["value"] = value
        if env_var := getattr(args, "env_var", None):
            if not _ENV_NAME.fullmatch(env_var):
                raise ValueError("--env-var must be a valid environment variable name")
            params["env_var"] = env_var
    return params


def _connect():
    from gateway import host_rendezvous as hr
    from hermes_cli.session_host import HostUnavailable, connect
    from hermes_constants import get_default_hermes_root

    record = hr.read_record(hr.ROLE_SERVE)
    if record is None:
        raise HostUnavailable(
            "No logged-in session host is running; start 'hermes --tui' or 'hermes serve' and unlock it"
        )
    root = get_default_hermes_root().resolve()
    if not record.home:
        raise HostUnavailable(
            "Restart the logged-in session host to publish its Hermes root, then retry"
        )
    if Path(record.home).resolve() != root:
        raise HostUnavailable(
            "The session host belongs to a different Hermes root; use the host's profile/home"
        )
    # Never spawn a host from an agent's locked terminal child.
    return connect(start=False)


def _print(payload: dict) -> None:
    from hermes_cli.mcp_config import redact_mcp_probe_text

    # The host's test path already redacts probe failures; also cover transport
    # and validation errors before this machine-readable response reaches a model.
    def safe(value):
        if isinstance(value, str):
            return redact_mcp_probe_text(value)
        if isinstance(value, dict):
            return {k: safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [safe(v) for v in value]
        return value

    print(json.dumps(safe(payload), ensure_ascii=False))


def mcp_host_command(args) -> int:
    from hermes_cli.profiles import get_active_profile_name
    from hermes_cli.session_host import HostRpcError, HostUnavailable
    from websockets.exceptions import WebSocketException

    try:
        profile = get_active_profile_name()
        if profile == "custom":
            raise ValueError(
                "Use a profile/home belonging to the logged-in session host"
            )
        params = _params(args, profile)
        with _connect() as client:
            result = client.call(HOST_ACTIONS[args.mcp_action], params, timeout=360)
        result = {"ok": True, **result, "profile": profile}
        if args.mcp_action in {"add", "remove", "rm", "set-api-key"}:
            result["activation"] = "next_session"
        _print(result)
        return 0 if result["ok"] else 1
    except ValueError as exc:
        _print({"ok": False, "error": str(exc)})
        return 2
    except (HostRpcError, HostUnavailable) as exc:
        _print({
            "ok": False,
            "error": str(exc),
            **({"code": exc.code} if isinstance(exc, HostRpcError) else {}),
        })
        return 1
    except (OSError, TimeoutError, WebSocketException) as exc:
        # Socket exception text can contain the authenticated URL.
        _print({
            "ok": False,
            "error": f"Session host connection failed ({type(exc).__name__}); reconnect and retry",
        })
        return 1
