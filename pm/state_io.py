"""Vault I/O stays in the owner; independent PM workers use their private pipe.

Publication still owns the install lock, ciphertext snapshots and crash journal.
Only envelope operations cross this boundary; the worker never holds vault keys.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_owner = ContextVar("pm_state_io_owner", default=None)


@contextmanager
def worker_state_io(callback):
    token = _owner.set(callback)
    try:
        yield
    finally:
        _owner.reset(token)


def vault_home(path: Path) -> Path | None:
    return next((parent for parent in path.resolve().parents if (parent / ".hermes-vault").is_file()), None)


def _vault(home: Path):
    # Durable launchers ask about currency before application activation. Only
    # their owning process may activate that graph; workers stay independent.
    import importlib.util

    if importlib.util.find_spec("cryptography") is None:
        from pm.environments import activate_dependencies
        from pm.paths import repo_root

        activate_dependencies(repo_root())
    from hermes_security.vault import get_vault
    from hermes_security.errors import VaultLockedError
    from hermes_security.handoff import adopt_inherited_key

    adopt_inherited_key()
    try:
        return get_vault(home)
    except VaultLockedError:
        from hermes_cli.vault_gate import _tty_available

        if not _tty_available():
            raise
        from hermes_cli.vault_cmd import gate_locked_vault

        gate_locked_vault(None, home)
        return get_vault(home)


def envelope_home(operation: str, path: str, purpose: str) -> Path:
    """Authorize live config/receipt paths, then resolve their owning vault.

    A named profile can share its parent's vault; state scope is not key scope.
    """
    from pm.plugins_state import dependency_homes

    target = Path(path).resolve()
    homes = dependency_homes()
    configs = {home.resolve() / "config.yaml" for home in homes}
    receipts = {home.resolve() / "logs" / "update_receipts" for home in homes}
    allowed = ((purpose == "config" and target in configs)
               or (purpose == "state" and target.parent in receipts and target.suffix == ".json"))
    if not allowed or operation not in {"decrypt", "encrypt"}:
        raise ValueError(f"not an authorized update envelope operation: {operation} {purpose} {target}")
    home = vault_home(target)
    if home is None:
        raise ValueError(f"PM state vault disappeared: {target}")
    return home


def owner_operation(operation: str, path: str, payload: str, purpose: str) -> str:
    from hermes_cli.update_vault import has_owner, envelope_operation

    if has_owner():
        return envelope_operation(operation, path, payload, purpose)
    target = Path(path).resolve()
    home = envelope_home(operation, path, purpose)
    vault = _vault(home)
    data = getattr(vault, operation)(base64.b64decode(payload, validate=True), purpose=purpose,
                                    relpath=target.relative_to(home).as_posix())
    return base64.b64encode(data).decode("ascii")


def prepare_owner() -> None:
    """Unlock before a worker takes the install lock, never from its callback."""
    from hermes_cli.update_vault import has_owner

    if has_owner():
        return
    from pm.plugins_state import dependency_homes

    for home in {vault_home(home / "config.yaml") for home in dependency_homes()} - {None}:
        _vault(home)


def _transform(operation: str, path: Path, data: bytes, purpose: str) -> bytes:
    callback = _owner.get() or owner_operation
    result = callback(operation, str(path.resolve()), base64.b64encode(data).decode("ascii"), purpose)
    return base64.b64decode(result, validate=True)


def decode(path: Path, data: bytes, *, purpose: str) -> str:
    if vault_home(path) is not None:
        data = _transform("decrypt", path, data, purpose)
    return data.decode("utf-8-sig")


def encode(path: Path, text: str, *, purpose: str) -> bytes:
    data = text.encode("utf-8")
    return _transform("encrypt", path, data, purpose) if vault_home(path) is not None else data


def read_text(path: Path, *, purpose: str) -> str:
    return decode(path, path.read_bytes(), purpose=purpose)
