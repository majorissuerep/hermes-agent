"""Installed runtime lookup uses the same shipped/addition precedence as ensure."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

import pm
from pm import paths
from pm.install import ensure
from pm.lock import Lockfile
from tests.pm.test_pm_authority import core_env as core_env, pm_env as pm_env, served as served  # noqa: PLC0414 -- pytest fixture exports; explicit aliases prevent F811 on fixture parameters


def test_installed_lookup_prefers_current_pin_then_recorded_fallback(pm_env, tmp_path, monkeypatch):
    ensure("faketool", explicit=True)
    original = pm.installed_package("faketool")
    assert original is not None and original.binary.is_file()

    lock = Lockfile(pm_env["lockfile_path"])
    lock.set_pin("faketool", "2.0", {"any": {
        "url": pm_env["base_url"] + "/faketool-2.0.tar.gz", "sha256": "b" * 64,
    }})
    lock.save()
    assert pm.installed_package("faketool") is None
    assert pm.installed_package("faketool", allow_outdated=True) == original

    shipped = tmp_path / "sealed" / "tools"
    shipped.mkdir(parents=True)
    monkeypatch.setattr(paths, "store_root", lambda: shipped)
    monkeypatch.setattr(paths, "writable_store_root", lambda: pm_env["runtime"])
    assert pm.installed_package("faketool", allow_outdated=True) == original
    original.binary.unlink()
    assert pm.installed_package("faketool", allow_outdated=True) is None


def test_state_fact_in_shared_facts_file_is_not_a_store_install(pm_env):
    """``venv`` is recorded via ``record_state`` (stamp + extras, no entry) into
    the same facts.json as tool facts. Reading the composed env over every
    registered package must skip it, not KeyError on ``fact["entry"]`` —
    that crashed ``activate`` (``pm.environments``) after a sync."""
    from pm.install import env_for
    from pm.lock import Facts
    from pm.registry import all_packages

    ensure("faketool", explicit=True)
    facts = Facts(paths.facts_path())
    facts.record_state("venv", "stamp", ["all"])

    assert not facts.installed("venv", None, paths.store_root())
    composed = env_for("venv", "faketool", *all_packages())
    assert composed == env_for("faketool")


def test_verified_lookup_refuses_changed_bytes_before_a_probe_can_execute(pm_env, tmp_path, monkeypatch):
    """Security checkers must not execute a modified tool merely to reject it."""
    from pm.registry import get_package

    ensure("faketool", explicit=True)
    original = pm.installed_package("faketool", verify=True)
    assert original is not None
    facts_before = paths.facts_path().read_bytes()
    marker = tmp_path / "untrusted-probe-executed"
    changed = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n".encode()
    original.binary.write_bytes(changed)

    def probe(entry, target):
        subprocess.run([sys.executable, str(original.binary)], check=True, timeout=10)
        return ""

    monkeypatch.setattr(get_package("faketool"), "verify", probe)
    assert pm.installed_package("faketool", verify=True) is None
    assert not marker.exists()
    assert original.binary.read_bytes() == changed
    assert paths.facts_path().read_bytes() == facts_before


@pytest.mark.skipif(os.name == "nt", reason="POSIX exec bit")
def test_entry_staged_without_exec_bit_is_usable_again(pm_env):
    """agent-browser entries staged before stage() set the exec bit raised PermissionError on every launch."""
    ensure("faketool", explicit=True)
    binary = pm.installed_package("faketool").binary
    binary.chmod(0o644)
    healed = pm.installed_package("faketool")
    assert healed is not None and healed.binary == binary and os.access(binary, os.X_OK)
