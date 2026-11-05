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


@pytest.mark.platforms("linux", "macos")
@pytest.mark.parametrize("replacement", ["foreign-link", "changed-regular"])
def test_verified_lookup_refuses_without_chmod_of_unverified_bytes(pm_env, tmp_path, replacement):
    ensure("faketool", explicit=True)
    original = pm.installed_package("faketool")
    if replacement == "foreign-link":
        selected = tmp_path / "foreign-user-data"
        selected.write_bytes(b"public synthetic retained user bytes")
        original.binary.unlink()
        original.binary.symlink_to(selected)
    else:
        selected = original.binary
        selected.write_bytes(b"changed public synthetic payload")
    selected.chmod(0o600)
    before, facts = selected.read_bytes(), paths.facts_path().read_bytes()

    assert pm.installed_package("faketool", verify=True) is None

    assert selected.read_bytes() == before and selected.stat().st_mode & 0o777 == 0o600
    assert paths.facts_path().read_bytes() == facts


@pytest.mark.platforms("linux", "macos")
def test_verified_lookup_keeps_recorded_in_store_executable_symlinks(pm_env):
    """Python's bin/python3-style archive links are valid package data, not blanket refusal."""
    from pm.lock import Facts
    from pm.store import tree_digest

    ensure("faketool", explicit=True)
    original = pm.installed_package("faketool")
    destination = original.binary.with_name("recorded-real-tool")
    original.binary.rename(destination)
    original.binary.symlink_to(destination.name)
    facts = Facts(paths.facts_path())
    fact = facts.get("faketool")
    facts.record("faketool", original.version, fact["entry"], {}, pm_env["runtime"],
                 target=fact["target"], artifacts=fact["artifacts"], digest=tree_digest(original.path))
    before = destination.stat().st_mode
    recorded = paths.facts_path().read_bytes()

    verified = pm.installed_package("faketool", verify=True)

    assert verified is not None and verified.binary == original.binary
    assert verified.binary.resolve() == destination and destination.stat().st_mode == before
    assert paths.facts_path().read_bytes() == recorded


@pytest.mark.platforms("linux", "macos")
def test_verified_read_is_nonmutating_but_explicit_pm_heals_valid_regular_exec_bits(pm_env):
    ensure("faketool", explicit=True)
    binary = pm.installed_package("faketool").binary
    before = binary.stat().st_ino
    payload = binary.read_bytes()
    binary.chmod(0o600)
    assert pm.installed_package("faketool", verify=True) is None
    assert binary.stat().st_mode & 0o777 == 0o600

    ensure("faketool", explicit=True)

    assert binary.stat().st_ino == before and binary.read_bytes() == payload
    assert os.access(binary, os.X_OK) and pm.installed_package("faketool", verify=True) is not None
