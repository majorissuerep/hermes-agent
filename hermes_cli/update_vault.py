"""Private vault transport across source-update interpreters.

Preparation runs without site-packages. Its envelope operations stay in the
unlocked parent; only the selected application interpreter adopts vault keys.
The channel is an inherited OS pipe, never a password or key in the environment,
request files, or a package-manager worker.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from contextvars import copy_context
import json
import os
from pathlib import Path
import sys
import threading

_CHANNEL = "HERMES_UPDATE_VAULT_CHANNEL"
_parent = None
_keys: list[dict] = []
_exchange_lock = threading.Lock()


def _send(connection, value):
    connection.send_bytes(json.dumps(value).encode("utf-8"))


def _receive(connection):
    return json.loads(connection.recv_bytes().decode("utf-8"))


def has_owner() -> bool:
    global _parent, _keys
    handle = os.environ.pop(_CHANNEL, None)
    if handle is not None:
        if os.name == "nt":
            from multiprocessing.connection import PipeConnection as Connection
        else:
            from multiprocessing.connection import Connection
        _parent = Connection(int(handle))
        _keys = _receive(_parent)
    return _parent is not None


def envelope_operation(operation: str, path: str, payload: str, purpose: str) -> str:
    """Proxy only the PM envelope protocol, without loading application imports."""
    if not has_owner():
        raise RuntimeError("update vault owner is unavailable")
    with _exchange_lock:
        _send(_parent, [operation, path, payload, purpose])
        result = _receive(_parent)
    if "error" in result:
        raise RuntimeError(result["error"])
    return result["value"]


def adopt_keys() -> None:
    """Called after activating the selected interpreter, before state readers."""
    if has_owner():
        from hermes_security.vault import adopt_unlocked_key

        for entry in _keys:
            adopt_unlocked_key(entry["home"], base64.b64decode(entry["key"], validate=True))


def _serve(connection, keys, vault_module, inherited):
    from pm.state_io import envelope_home

    homes = {Path(entry["home"]).resolve() for entry in keys}
    try:
        _send(connection, keys)
        while True:
            operation, path, payload, purpose = _receive(connection)
            try:
                target = Path(path).resolve()
                home = envelope_home(operation, path, purpose)
                if home not in homes:
                    raise ValueError("not an authorized update envelope operation")
                if inherited:
                    value = envelope_operation(operation, path, payload, purpose)
                else:
                    vault = vault_module.get_vault(home, allow_env_unlock=False)
                    data = getattr(vault, operation)(base64.b64decode(payload, validate=True),
                                                    purpose=purpose, relpath=target.relative_to(home).as_posix())
                    value = base64.b64encode(data).decode("ascii")
                result = {"value": value}
            except Exception as exc:
                result = {"error": f"{type(exc).__name__}: {exc}"}
            _send(connection, result)
    except (EOFError, BrokenPipeError, OSError):
        pass  # The exclusively owned child has exited or failed to spawn.
    finally:
        connection.close()


@contextmanager
def child_vault(env: dict[str, str]):
    """Popen kwargs for one trusted update child; works before/after a source swap.

    Only ALREADY LOADED vault code is used in the pre-swap parent. A preparation
    process forwards the same authority without importing cryptography itself.
    """
    inherited = has_owner()
    vault_module = sys.modules.get("hermes_security.vault")
    keys = list(_keys) if inherited else []
    if not inherited and vault_module is not None:
        keys = [{"home": str(home), "key": base64.b64encode(
            vault_module.export_unlocked_key(home)).decode("ascii")}
            for home in vault_module.unlocked_homes()]
    env.pop(_CHANNEL, None)
    if not keys:
        yield {}
        return

    from multiprocessing import Pipe

    parent, child = Pipe(duplex=True)
    handle = child.fileno()
    if os.name == "nt":
        import subprocess

        os.set_handle_inheritable(handle, True)
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [handle]}
        kwargs = {"startupinfo": startup}
    else:
        kwargs = {"pass_fds": (handle,)}
    env[_CHANNEL] = str(handle)
    context = copy_context()
    thread = threading.Thread(target=context.run, args=(_serve, parent, keys, vault_module, inherited),
                              name="update-vault", daemon=True)
    thread.start()
    try:
        yield kwargs
    finally:
        env.pop(_CHANNEL, None)
        child.close()
        thread.join(timeout=5)
