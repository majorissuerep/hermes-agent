"""Container acceptance exports committed source only and rejects incomplete proof."""
from copy import deepcopy
import hashlib
import json
import subprocess
import tarfile

import pytest

from scripts.e2e.container_lifecycle import SCENARIOS, export_source, verify_receipt


def test_source_export_is_self_contained_and_excludes_host_files_and_configuration(tmp_path):
    source = tmp_path / "source"
    source.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()

    git("init", "-b", "main")
    bootstrap = source / "scripts/e2e/container_bootstrap.sh"
    bootstrap.parent.mkdir(parents=True)
    bootstrap.write_text("#!/bin/sh\nexit 0\n")
    for name in ("encrypted_lifecycle.py", "state_probe.py", "container_lifecycle.py"):
        (bootstrap.parent / name).write_text("# committed fixture source\n")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "baseline")
    baseline = git("rev-parse", "HEAD")
    (source / "tracked").write_text("committed candidate")
    git("add", "tracked")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "candidate")
    candidate = git("rev-parse", "HEAD")
    git("config", "remote.personal.url", "https://synthetic-secret@example.invalid/private")
    (source / "untracked-private-state").write_text("must not leave the host")
    (source / "tracked").write_text("dirty working-tree bytes must not leave the host")
    archive = tmp_path / "source.tar"
    provenance = export_source(source, baseline, candidate, archive)
    unpacked = tmp_path / "unpacked"
    with tarfile.open(archive) as bundle:
        bundle.extractall(unpacked, filter="data")
    bare = unpacked / "source.git"
    exported = lambda *args: subprocess.check_output(["git", "--git-dir", str(bare), *args], text=True).strip()
    assert exported("rev-parse", "main", "baseline").splitlines() == [candidate, baseline]
    assert exported("show", "main:tracked") == "committed candidate"
    assert "personal" not in (bare / "config").read_text()
    assert "untracked-private-state" not in exported("ls-tree", "-r", "--name-only", "main")
    assert not (bare / "objects/info/alternates").exists()
    subprocess.run(["git", "--git-dir", str(bare), "fsck", "--full"], check=True, capture_output=True)
    assert provenance["bootstrap_sha256"] == hashlib.sha256(bootstrap.read_bytes()).hexdigest()
    assert json.loads((unpacked / "source.json").read_text())["candidate"] == candidate


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_claimed_green_receipts_require_real_update_delegation_restore_and_revocation(scenario):
    provenance = {"baseline": "old", "candidate": "new", "harness_sha256": "exact-harness"}
    receipt = {**provenance, "status": "passed", "scenario": scenario, "tool_cache_reused": False,
               "restore_verified": True, "cleanup_verified": True, "independent_locked_cli_rejected": True,
               "vault_repair_verified": True, "bulk_export_verified": True, "full_backup_verified": True,
               "installer_repair_verified": True,
               "upstream_baseline": "unmodified-upstream", "upstream_session_hash": {"messages": "retained"},
               "upstream_restore_verified": True, "upstream_backup_verified": True,
               "legacy_state_verified": True, "legacy_state_hashes": {"memory": "retained"},
               "checks": {"live-candidate": {"passphrase_prompts": 1, "child_sessions": 1,
                   "verified_hermes_calls": 3, "complete": True, "artifact_verified": True,
                   "executed_in_child": {"hermes config get agent.max_turns": True}},
                   "update": {"passphrase_prompts": 1},
                   "resume-candidate": {"passphrase_prompts": 1, "history_verified": True, "artifact_verified": True}},
               "update_status": {"outcome": "success", "followups": []}}
    verify_receipt(receipt, scenario, provenance)
    mutations = [("candidate", "another-head"), ("restore_verified", False), ("cleanup_verified", False),
                 ("vault_repair_verified", False), ("bulk_export_verified", False), ("full_backup_verified", False),
                 ("tool_cache_reused", True), ("independent_locked_cli_rejected", False), ("checks", {})]
    if scenario in {"dirty-install", "upstream-install"}:
        mutations.append(("legacy_state_verified", False))
    if scenario == "reinstall":
        mutations.append(("installer_repair_verified", False))
    if scenario == "upstream-install":
        mutations.append(("upstream_session_hash", None))
        mutations.append(("upstream_restore_verified", False))
        mutations.append(("upstream_backup_verified", False))
    if scenario in {"update", "broken-files"}:
        mutations.append(("update_status", {"outcome": "success", "followups": ["unfinished"]}))
    for key, value in mutations:
        damaged = deepcopy(receipt)
        damaged[key] = value
        with pytest.raises(ValueError):
            verify_receipt(damaged, scenario, provenance)
    damaged = deepcopy(receipt)
    damaged["checks"]["live-candidate"]["passphrase_prompts"] += 1
    with pytest.raises(ValueError):
        verify_receipt(damaged, scenario, provenance)
