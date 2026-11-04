"""Management reports resource protection without upgrading a borrowed requester's authority."""
from __future__ import annotations

import json

import pytest

pytest_plugins = ["tests.hermes_cli.test_management_public_cli"]
pytestmark = [pytest.mark.platforms("linux", "macos"), pytest.mark.live_system_guard_bypass]


def test_public_status_reports_observed_protection_without_owner_consent(world):
    from hermes_platform.memory_protection import core_dump_limits, memlock_limits, swap_state
    from hermes_security import vault

    result, output = world.call({"op": "status"})
    assert result.returncode == 0, world.diagnostic(output)
    status = output["result"]
    assert status["status_version"] == 2
    assert status["home"] == str(world.home) and status["unlocked"] is True
    assert status["resource_scope"] == {
        "profile_home": str(world.home), "vault_home": str(world.home),
        "authority": "local_credential", "protection_observed_in": "responding_process",
    }
    assert status["requester"] == {
        "scope": "operations_only", "owner_consent": "not_asserted",
        "raw_key_export": False, "passphrase_change": "refused",
    }
    protection = status["protection"]
    custody = vault.get_vault(world.home).protection_status()
    assert protection["retained_key"] == {
        "locked": custody["retained_key_locked"], "dontdump": custody["retained_key_dontdump"],
        "wiped": False, "coverage": "retained_key_only",
    }
    assert protection["retained_key"]["locked"] is True
    for name, reader in (("memlock_limits", memlock_limits), ("core_dumps", core_dump_limits)):
        soft, hard = reader()
        assert protection[name]["observed"] is True
        assert protection[name]["soft_bytes"] == (soft if soft >= 0 else None)
        assert protection[name]["hard_bytes"] == (hard if hard >= 0 else None)
    assert protection["swap"] == {"state": swap_state(), "encrypted_swap_evidence": "unknown",
                                  "transient_buffers_unswappable": "not_established"}
    assert protection["core_dumps"]["piped_collector_policy"] == "unknown"
    assert protection["core_dumps"]["crash_store_encryption_evidence"] == "unknown"
    assert protection["workflow_admission"] == "not_evaluated"
    assert protection["protected_work_dir"]["assessment"] == "read_only_no_creation"
    rendered = json.dumps(output) + result.stderr
    assert world.password not in rendered
    assert world.env["HERMES_SESSION_VAULT"] not in rendered
    result, schema = world.call()
    assert result.returncode == 0
    assert {"consent_required", "owner_restart_required"} <= set(schema["result"]["errors"])


def test_status_keeps_missing_host_evidence_unknown_and_workdir_probe_read_only(world, monkeypatch, tmp_path):
    from hermes_cli.management_status import workspace_status
    from hermes_platform import memory_protection
    from hermes_platform.memory_protection import RamProof
    from hermes_security import protected_workspace

    def unavailable_limits():
        raise OSError("synthetic unavailable host fact")

    monkeypatch.setattr(memory_protection, "core_dump_limits", unavailable_limits)
    monkeypatch.setattr(memory_protection, "memlock_limits", unavailable_limits)
    monkeypatch.setattr(memory_protection, "swap_state", lambda: "unknown")
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    result, output = world.call({"op": "status"})
    assert result.returncode == 0, world.diagnostic(output)
    protection = output["result"]["protection"]
    assert protection["swap"]["state"] == "unknown"
    assert protection["memlock_limits"]["observed"] is False
    assert protection["memlock_limits"]["soft_bytes"] is None
    assert protection["core_dumps"]["soft_limit_zero"] is None
    workdir = protection["protected_work_dir"]
    assert workdir["availability"] == "unavailable"
    assert workdir["next_config_command"][:4] == ["hermes", "config", "set", "security.protected_work_dir"]
    missing = tmp_path / "not-created"
    monkeypatch.setattr(protected_workspace, "protected_workspace_base", lambda: missing)
    monkeypatch.setattr(protected_workspace, "protected_workspace_status", lambda base: RamProof(
        True, "ramfs", "synthetic proof of the containing mount"))
    observation = workspace_status()
    assert observation["ram_only_proven"] is True
    assert observation["availability"] == "unavailable"
    assert not missing.exists()
