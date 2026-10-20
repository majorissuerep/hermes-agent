"""Typed, comment-preserving management transactions for one encrypted config file."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit


class ManagementError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _sensitive_key(key):
    if key.lower().endswith(("_env", "_env_var", "_env_name")):
        return False
    return bool(re.search(r"(?:^|[_-])(?:key|api_?key|token|password|secret|credentials?|authorization|headers?)(?:$|[_-])", key, re.IGNORECASE))


def revision(path):
    return hashlib.sha256(path.read_bytes() if path.exists() else b"").hexdigest()


def checked_path(value, *, root=False):
    if not isinstance(value, list) or len(value) > 32 or (not value and not root):
        raise ManagementError("invalid_path", "path must be a nonempty array of keys or list indices")
    if any(not ((isinstance(key, str) and key and "\0" not in key) or (type(key) is int and key >= 0)) for key in value):
        raise ManagementError("invalid_path", "path segments must be nonempty strings or nonnegative integers")
    return value


def at_path(data, path):
    for key in path:
        if isinstance(data, dict) and isinstance(key, str) and key in data or isinstance(data, list) and type(key) is int and key < len(data):
            data = data[key]
        else:
            raise ManagementError("not_found", "configuration path does not exist")
    return data


def redacted_config(value, key="", sensitive=False):
    """Inspect raw, never expanded YAML; arbitrary headers and URL credentials stay private."""
    sensitive = sensitive or _sensitive_key(key)
    if isinstance(value, dict):
        return {k: redacted_config(v, str(k), sensitive) for k, v in value.items()}
    if isinstance(value, list):
        return [redacted_config(v, key, sensitive) for v in value]
    if sensitive or (isinstance(value, str) and "${" in value):
        return "[redacted]" if value is not None else None
    if isinstance(value, str) and "://" in value:
        try:
            parts = urlsplit(value)
            host = parts.netloc.rsplit("@", 1)[-1]
            return urlunsplit((parts.scheme, host, parts.path, "[redacted]" if parts.query else "", ""))
        except ValueError:
            return "[redacted]"
    return value


def _parent(data, path, *, create):
    current = data
    for key in path[:-1]:
        if isinstance(current, dict) and isinstance(key, str) and key not in current and create:
            current[key] = {}
        current = at_path(current, [key])
    return current, path[-1]


def _change(data, operation):
    op = operation.get("op")
    allowed = {"op", "path", "value"} if op == "config.set" else {"op", "path"}
    if op not in {"config.set", "config.unset"} or set(operation) - allowed:
        raise ManagementError("invalid_operation", "batch accepts only config.set/config.unset")
    path = checked_path(operation.get("path"))
    _guard_managed(path)
    from hermes_cli.config_env_routing import is_env_setting_key
    if len(path) == 1 and isinstance(path[0], str) and is_env_setting_key(path[0]):
        raise ManagementError("secret_operation_required", "use secret.set or secret.unset for environment secrets")
    if len(path) > 1 and path[0] == "model" and isinstance(data.get("model"), str):
        data["model"] = {"default": data["model"]}
    parent, leaf = _parent(data, path, create=op == "config.set")
    if not ((isinstance(parent, dict) and isinstance(leaf, str)) or
            (isinstance(parent, list) and type(leaf) is int and leaf < len(parent))):
        raise ManagementError("invalid_path", "path does not address a mapping key or existing list element")
    if op == "config.unset":
        at_path(parent, [leaf])
        del parent[leaf]
        return
    if "value" not in operation:
        raise ManagementError("invalid_value", "config.set requires a JSON value")
    for segment in path:
        _refuse_literal_secrets(operation["value"], str(segment))
    parent[leaf] = operation["value"]


def _refuse_literal_secrets(value, key="", sensitive=False):
    credential_fields = {"api_key", "access_token", "refresh_token", "password", "passphrase", "client_secret",
                         "secret_key", "token", "authorization", "proxy_authorization", "x_api_key", "x_auth_token"}
    sensitive = sensitive or key.lower().replace("-", "_") in credential_fields
    if isinstance(value, dict):
        for name, child in value.items():
            _refuse_literal_secrets(child, str(name), sensitive)
    elif isinstance(value, list):
        for child in value:
            _refuse_literal_secrets(child, key, sensitive)
    elif sensitive and value not in (None, "") and not (isinstance(value, str) and re.fullmatch(r"(?:Bearer )?\$\{[A-Za-z_][A-Za-z0-9_]*\}", value)):
        raise ManagementError("secret_operation_required", "store secrets with secret.set and configure an environment reference")


def _validate(data):
    from hermes_cli.config import DEFAULT_CONFIG, validate_config_structure
    # Defaults are a partial schema, not an allow-list: plugin/custom keys remain valid.
    def types(actual, defaults):
        if not isinstance(actual, dict) or not isinstance(defaults, dict):
            return
        for key in actual.keys() & defaults.keys():
            value, expected = actual[key], defaults[key]
            if key == "model" and isinstance(value, dict) and isinstance(expected, str):
                continue
            if expected is None or value is None:
                continue
            if type(expected) is float and type(value) in (int, float):
                continue
            if not isinstance(value, type(expected)) or (type(expected) is int and type(value) is bool):
                raise ManagementError("invalid_type", "configuration value has an incompatible type")
            types(value, expected)
    types(data, DEFAULT_CONFIG)
    if any(issue.severity == "error" for issue in validate_config_structure(data)):
        raise ManagementError("invalid_config", "configuration failed canonical structural validation")
    json.dumps(data, allow_nan=False)


def _guard_managed(path):
    from hermes_cli import config, managed_scope
    dotted = ".".join(map(str, path))
    if config.is_managed() or any(dotted == key or dotted.startswith(key + ".") or key.startswith(dotted + ".")
                                  for key in managed_scope.managed_config_keys()):
        raise ManagementError("state_refused", "configuration is managed by the installation or administrator")


def config_operation(home, request):
    from hermes_cli import config
    path = home / "config.yaml"
    # The canonical lock also serializes legacy writers; CAS spans the complete read/validate/write.
    from hermes_cli.config_transactions import config_transaction
    with config_transaction(path):
        before = revision(path)
        data = config.require_readable_config_before_write(path)
        op = request["op"]
        if op == "config.get":
            selected = checked_path(request.get("path", []), root=True)
            return {"value": at_path(redacted_config(data), selected), "revision": before}
        if request.get("revision") != before:
            raise ManagementError("conflict", "configuration changed; read its current revision before writing")
        candidate = copy.deepcopy(data)
        operations = request.get("operations") if op == "config.batch" else [
            {k: v for k, v in request.items() if k not in {"revision"}}]
        if not isinstance(operations, list) or not operations or len(operations) > 100:
            raise ManagementError("invalid_operation", "batch requires 1..100 config operations")
        for operation in operations:
            if not isinstance(operation, dict):
                raise ManagementError("invalid_operation", "each batch operation must be an object")
            _change(candidate, operation)
        _sync_route(data, candidate, operations)
        _validate(candidate)
        if revision(path) != before:
            raise ManagementError("conflict", "configuration changed during validation")
        config.atomic_config_replace(path, candidate)
        return {"revision": revision(path), "changed": candidate != data, "effect": "next_session"}


def _sync_route(previous, proposed, operations):
    old, new = previous.get("model"), proposed.get("model")
    explicit_route = any(operation.get("path", [])[:2] in (["model", "base_url"], ["model", "api_mode"])
                         or operation.get("path") == ["model"] for operation in operations)
    if (isinstance(old, dict) and isinstance(new, dict) and old.get("provider") != new.get("provider")
            and not explicit_route):
        from hermes_cli.route_identity import drop_stale_model_route
        drop_stale_model_route(new, new.get("provider"), proposed)
