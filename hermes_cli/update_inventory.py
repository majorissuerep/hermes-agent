"""Runtime inventory + update plan for the fleet-update pipeline.

One read-only pass answering, BEFORE any mutation: which Hermes runtimes run on this machine, how
each is deployed, which ones this update touches, and how each restarts. Every collector is a
side-effect-free probe, so ``hermes update --plan`` is safe on a live fleet.
"""

from __future__ import annotations

import logging
import shlex
import sys
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, asdict, fields as dataclass_fields
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class RuntimeRecord:
    """One running (or expected) Hermes runtime on this machine."""

    kind: str                     # gateway | dashboard | serve
    profile: str
    pid: Optional[int] = None
    supervisor: str = "manual"    # systemd | launchd | desktop | windows-service | service | manual | manual-serve
    code_sha: Optional[str] = None       # stamped running-code sha
    # See #91283.
    code_version: Optional[str] = None
    restart_via: str = ""         # mechanism id, see _RESTART_MECHANISMS
    detail: dict = field(default_factory=dict)


@dataclass
class UpdatePlan:
    """The full pre-update picture: install shape + runtimes + actions."""

    install_method: str = "unknown"       # git | docker | nix | apt | ...
    updatable_in_place: bool = True
    update_mechanism: str = "hermes update"
    expected_sha: Optional[str] = None    # current checkout HEAD (pre-pull)
    expected_version: Optional[str] = None
    profiles: list = field(default_factory=list)
    runtimes: list = field(default_factory=list)  # list[RuntimeRecord]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # recursive: RuntimeRecord entries become dicts

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UpdatePlan:
        """Inverse of :meth:`to_dict` (the plan crosses the post-swap hand-off as JSON)."""
        fields_ = {f.name for f in dataclass_fields(cls)}
        plan = cls(**{k: v for k, v in data.items() if k in fields_ and k != "runtimes"})
        record_fields = {f.name for f in dataclass_fields(RuntimeRecord)}
        plan.runtimes = [
            RuntimeRecord(**{k: v for k, v in r.items() if k in record_fields})
            for r in data.get("runtimes") or [] if isinstance(r, dict)
        ]
        return plan


def _detect_supervisor_for_pid(*args, **kwargs):
    """Frozen old updater entry; reload the terminal completion implementation."""
    from hermes_cli._old_updater import stop_for_relaunch
    stop_for_relaunch()

def _restart_mechanism(*args, **kwargs):
    """Frozen old updater entry; reload the terminal completion implementation."""
    from hermes_cli._old_updater import stop_for_relaunch
    stop_for_relaunch()

def describe_restart_mechanism(*args, **kwargs):
    """Frozen old updater entry; reload the terminal completion implementation."""
    from hermes_cli._old_updater import stop_for_relaunch
    stop_for_relaunch()

def _runtime(*args, **kwargs):
    """Frozen old updater entry; reload the terminal completion implementation."""
    from hermes_cli._old_updater import stop_for_relaunch
    stop_for_relaunch()

def _probe(*args, **kwargs):
    """Frozen old updater entry; reload the terminal completion implementation."""
    from hermes_cli._old_updater import stop_for_relaunch
    stop_for_relaunch()

def _collect_install_shape(*args, **kwargs):
    """Frozen old updater entry; reload the terminal completion implementation."""
    from hermes_cli._old_updater import stop_for_relaunch
    stop_for_relaunch()

def _supervisor_classifier(*args, **kwargs):
    """Frozen old updater entry; reload the terminal completion implementation."""
    from hermes_cli._old_updater import stop_for_relaunch
    stop_for_relaunch()

def _collect_gateway_runtimes(*args, **kwargs):
    """Frozen old updater entry; reload the terminal completion implementation."""
    from hermes_cli._old_updater import stop_for_relaunch
    stop_for_relaunch()

def _loaded_backend_launchd_jobs(*args, **kwargs):
    """Frozen old updater entry; reload the terminal completion implementation."""
    from hermes_cli._old_updater import stop_for_relaunch
    stop_for_relaunch()

def _launchd_owner_for_ledger_entry(*args, **kwargs):
    """Frozen old updater entry; reload the terminal completion implementation."""
    from hermes_cli._old_updater import stop_for_relaunch
    stop_for_relaunch()

def _is_desktop_ssh_ledger_entry(*args, **kwargs):
    """Frozen old updater entry; reload the terminal completion implementation."""
    from hermes_cli._old_updater import stop_for_relaunch
    stop_for_relaunch()

def _collect_ledger_runtimes(*args, **kwargs):
    """Frozen old updater entry; reload the terminal completion implementation."""
    from hermes_cli._old_updater import stop_for_relaunch
    stop_for_relaunch()

def collect_runtime_inventory(*args, **kwargs):
    """Frozen old updater entry; reload the terminal completion implementation."""
    from hermes_cli._old_updater import stop_for_relaunch
    stop_for_relaunch()

def print_update_plan(*args, **kwargs):
    """Frozen old updater entry; reload the terminal completion implementation."""
    from hermes_cli._old_updater import stop_for_relaunch
    stop_for_relaunch()

def _serve_unit_matches_profile(*args, **kwargs):
    """Frozen old updater entry; reload the terminal completion implementation."""
    from hermes_cli._old_updater import stop_for_relaunch
    stop_for_relaunch()

def _gateway_service_matches_profile(*args, **kwargs):
    """Frozen old updater entry; reload the terminal completion implementation."""
    from hermes_cli._old_updater import stop_for_relaunch
    stop_for_relaunch()

def _gateway_named_in(*args, **kwargs):
    """Frozen old updater entry; reload the terminal completion implementation."""
    from hermes_cli._old_updater import stop_for_relaunch
    stop_for_relaunch()

def match_runtime_outcomes(*args, **kwargs):
    """Frozen old updater entry; reload the terminal completion implementation."""
    from hermes_cli._old_updater import stop_for_relaunch
    stop_for_relaunch()

def report_unaccounted_runtimes(*args, **kwargs):
    """Frozen old updater entry; reload the terminal completion implementation."""
    from hermes_cli._old_updater import stop_for_relaunch
    stop_for_relaunch()

def record_plan_in_receipt(*args, **kwargs):
    """Frozen old updater entry; reload the terminal completion implementation."""
    from hermes_cli._old_updater import stop_for_relaunch
    stop_for_relaunch()
