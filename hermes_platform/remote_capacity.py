"""Run the exact public RAM-capacity dependency closure inside an outgoing target.

Only Python's isolated interpreter, public module source and native facts cross this
boundary. No target files, Hermes home, secrets or substitute capacity policy are used.
The caller must guard this command with the same immutable target generation as its transfer.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

_MODULES = ("memory_protection", "runtime_health", "memory_capacity")
_SOURCE_BOUND = 64 * 1024
_PROGRAM = r'''
import hashlib, json, os, sys, types
from pathlib import Path
if sys.version_info < (3, 11):
    raise RuntimeError("protected browser continuity requires Python 3.11 or later")
raw = sys.stdin.buffer.read(65537)
if len(raw) > 65536:
    raise ValueError("public capacity module closure exceeds its bound")
modules = json.loads(raw)
if [name for name, source in modules] != ["memory_protection", "runtime_health", "memory_capacity"]:
    raise ValueError("unexpected public capacity dependency closure")
package = types.ModuleType("hermes_platform")
package.__path__ = []
sys.modules[package.__name__] = package
for name, source in modules:
    module = types.ModuleType("hermes_platform." + name)
    sys.modules[module.__name__] = module
    setattr(package, name, module)
    exec(compile(source, "<public-hermes-capacity-" + name + ">", "exec"), module.__dict__)
budget = package.memory_capacity.capacity_budget(Path(sys.argv[1]))
machine_path = Path("/etc/machine-id")
machine = b""
if machine_path.is_file():
    with machine_path.open("rb") as machine_file:
        machine = machine_file.read(129).strip()
if len(machine) > 128:
    raise ValueError("native machine identity exceeds its bound")
print(json.dumps({"available_bytes": budget.available_bytes, "reason": budget.reason,
                  "uid": os.geteuid(), "home": os.path.realpath(os.path.expanduser("~")),
                  "machine": hashlib.sha256(machine).hexdigest() if machine else None}))
'''


@dataclass(frozen=True)
class TargetCapacity:
    available_bytes: int | None
    reason: str
    uid: int
    home: str
    machine: str | None


def command(path: str) -> tuple[list[str], bytes, str]:
    """Public argv, bounded stdin module closure, and its SHA-256 (safe receipt identity)."""
    root = Path(__file__).parent
    payload = json.dumps([(name, (root / f"{name}.py").read_text(encoding="utf-8"))
                          for name in _MODULES]).encode()
    if len(payload) > _SOURCE_BOUND:
        raise ValueError("Public capacity dependency closure exceeds its supported bound")
    return ["python3", "-I", "-c", _PROGRAM, path], payload, hashlib.sha256(payload).hexdigest()


def result(raw: bytes) -> TargetCapacity:
    """Admit only a bounded native result, never treat missing evidence as unlimited RAM."""
    if len(raw) > 2048:
        raise ValueError("Target capacity report exceeds its bound")
    document = json.loads(raw)
    if not isinstance(document, dict):
        raise ValueError("Target capacity report is invalid")
    available, reason = document.get("available_bytes"), document.get("reason")
    uid, home, machine = document.get("uid"), document.get("home"), document.get("machine")
    if ((available is not None and (type(available) is not int or available < 0))
            or not isinstance(reason, str) or not reason or len(reason) > 1024):
        raise ValueError("Target capacity report is invalid")
    if (type(uid) is not int or uid < 0 or not isinstance(home, str) or not home.startswith("/")
            or len(home) > 4096 or "\x00" in home
            or (machine is not None and (not isinstance(machine, str) or len(machine) != 64
                                         or any(char not in "0123456789abcdef" for char in machine)))):
        raise ValueError("Target capacity identity is invalid")
    return TargetCapacity(available, reason, uid, home, machine)


def docker_daemon_command(executable: str) -> list[str]:
    """Read the selected daemon's public identity, distinct from an ephemeral container ID."""
    return [executable, "info", "--format", "{{.ID}}"]


def daemon_identity(raw: bytes) -> str:
    """No unbounded daemon output or empty identity may select a durable profile."""
    value = raw.strip()
    if not value or len(value) > 256 or any(c not in b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:-_" for c in value):
        raise ValueError("Selected Docker daemon has no bounded stable identity")
    return hashlib.sha256(value).hexdigest()
