"""Tests for hermes_cli.update_receipt — Phase 1 of the fleet-update plan (#91277).

Covers:
- receipt lifecycle (begin → record → finalize → read back)
- skip recording with reasons
- gateway restart phase recording (success + phase-error shapes)
- receipt pruning
- fleet version classification (current / stale / unknown)
- gateway_state.json code-identity stamping (gateway/status.py side)
"""

import json
import os
import sys
from types import SimpleNamespace

import pytest
from tests.hermes_cli.update_test_support import isolated_command_checkout as isolated_command_checkout  # noqa: PLC0414 -- register the shared autouse fixture


import hermes_cli.update_receipt as ur
from hermes_cli import update_cmd, update_cmd_maint


@pytest.fixture()
def receipt_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME for receipt writes."""
    home = tmp_path / ".hermes"
    home.mkdir()
    # ``_receipt_dir`` resolves through ``hermes_constants.get_hermes_home`` (env var), not
    # ``hermes_cli.config`` — patch where production reads.
    monkeypatch.setenv("HERMES_HOME", str(home))
    # The open receipt is per-context; the scope isolates it from any enclosing receipt.
    with ur.update_receipt_scope():
        yield home


def _finalize(outcome="success", fleet=None):
    return ur.finalize_update_receipt(outcome, fleet=fleet)


class TestReceiptLifecycle:
    def test_begin_record_finalize_roundtrip(self, receipt_home):
        ur.begin_update_receipt()
        ur.record_step("pre_update_backup", True, "snapshot=abc123")
        ur.record_skip("gateway_restart", "no gateways running")
        ur.record_gateway_restart(
            restarted_services=["hermes-gateway"],
            relaunched_profiles=["work"],
            killed_pids=[123],
            failed_units=[],
            incomplete=False,
        )
        path = _finalize("success", fleet=[{"profile": "default", "state": "current"}])
        assert path is not None and path.is_file()

        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["schema"] == 1
        assert payload["outcome"] == "success"
        assert payload["finished_at"] is not None
        assert payload["steps"][0]["name"] == "pre_update_backup"
        assert payload["steps"][0]["ok"] is True
        assert payload["skips"][0]["reason"] == "no gateways running"
        gr = payload["gateway_restart"]
        assert gr["restarted_services"] == ["hermes-gateway"]
        assert gr["relaunched_profiles"] == ["work"]
        assert gr["killed_pids"] == [123]
        assert gr["incomplete"] is False
        assert payload["fleet"][0]["profile"] == "default"

    def test_fresh_recovery_result_reaches_persisted_receipt(self, receipt_home):
        recovery = {
            "requested": ["coder", "default", "ops"],
            "verified": ["default"],
            "relaunch_attempted": ["ops"],
            "failed": ["coder"],
            "skipped": [
                {
                    "profile": "desk",
                    "kind": "serve",
                    "supervisor": "desktop",
                    "reason": "desktop app owns and respawns this serve backend",
                }
            ],
        }

        ur.begin_update_receipt()
        ur.record_gateway_restart(
            restarted_services=[],
            incomplete=True,
            phase_error="boom: module vanished mid-pull",
            fresh_recovery=recovery,
        )
        path = _finalize("partial")

        payload = json.loads(path.read_text(encoding="utf-8"))
        persisted = payload["gateway_restart"]["fresh_recovery"]
        assert {key: persisted[key] for key in recovery} == recovery
        # Serve coverage is always persisted, even when the pass had nothing
        # to report, so a reader can tell "no serve runtime" from "the field
        # predates #92145".
        assert persisted["serve_units"] == {"verified": [], "failed": []}
        assert persisted["stale_runtimes"] == []
        # The conservative vocabulary is the persisted contract: no bucket may
        # rebrand an unverified relaunch as supervisor-backed success.
        assert "succeeded" not in persisted

    def test_latest_pointer_written_and_readable(self, receipt_home):
        ur.begin_update_receipt()
        ur.record_step("git_pull", True)
        _finalize("partial")
        latest = ur.read_latest_receipt()
        assert latest is not None
        assert latest["outcome"] == "partial"


    def test_record_without_begin_is_noop(self, receipt_home):
        # No begin — nothing should raise, nothing should be written.
        ur.record_step("orphan", True)
        ur.record_skip("orphan", "no receipt")
        ur.record_gateway_restart(restarted_services=[])
        assert _finalize("success") is None


    def test_pruning_keeps_recent(self, receipt_home, monkeypatch):
        monkeypatch.setattr(ur, "_RECEIPT_KEEP", 3)
        directory = receipt_home / "logs" / "update_receipts"
        directory.mkdir(parents=True)
        for i in range(6):
            p = directory / f"update_2026010{i}_000000_1.json"
            p.write_text("{}", encoding="utf-8")
            os.utime(p, (1000 + i, 1000 + i))
        ur._prune_old_receipts(directory)
        remaining = sorted(p.name for p in directory.glob("update_*.json"))
        assert len(remaining) == 3
        # newest three survive
        assert remaining == [
            "update_20260103_000000_1.json",
            "update_20260104_000000_1.json",
            "update_20260105_000000_1.json",
        ]


class TestCommandBoundaryFinalization:
    """Receipt lifetime is owned by the update-command boundary (#91283 review).

    Early sys.exit paths (concurrent-instance preflight exit-2, venv-holder
    refusal, fetch failure) predate the inner finalize sites; the boundary
    safety net must persist the receipt exactly once with the stop reason,
    while inner-finalized runs are untouched.
    """

    def test_pending_receipt_persisted_on_exit_2_refusal(self, receipt_home):
        ur.begin_update_receipt()
        ur.record_step("windows_preflight", False, "another hermes.exe running")
        path = ur.finalize_pending_update_receipt(2, "sys.exit(2)")
        assert path is not None and path.is_file()
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["outcome"] == "refused"
        assert payload["exit_code"] == 2
        assert payload["stop_reason"] == "sys.exit(2)"
        assert payload["finished_at"] is not None
        assert ur.current_correlation_id() is None

    def test_pending_receipt_persisted_on_exit_1_failure(self, receipt_home):
        ur.begin_update_receipt()
        path = ur.finalize_pending_update_receipt(1, "sys.exit(1)")
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["outcome"] == "failed"
        assert payload["exit_code"] == 1

    def test_noop_when_inner_path_already_finalized(self, receipt_home):
        """Exactly-once: boundary call after an inner finalize writes nothing."""
        ur.begin_update_receipt()
        first = ur.finalize_update_receipt("success")
        assert first is not None
        second = ur.finalize_pending_update_receipt(0, "boundary")
        assert second is None
        directory = receipt_home / "logs" / "update_receipts"
        assert len(list(directory.glob("update_*.json"))) == 1

    def test_noop_when_never_begun(self, receipt_home):
        assert ur.finalize_pending_update_receipt(2, "sys.exit(2)") is None
        assert ur.read_latest_receipt() is None

    def test_cmd_update_boundary_finalizes_on_early_exit(
        self, receipt_home, monkeypatch
    ):
        """End-to-end through the real cmd_update wrapper: an impl that begins
        a receipt then sys.exit(2)s (the concurrent-instance shape) must leave
        a finalized 'refused' receipt, preserve the exit code, and clear the
        singleton."""
        from types import SimpleNamespace

        from hermes_cli import main as hermes_main

        def _fake_impl(args, gateway_mode=False):
            ur.begin_update_receipt()
            ur.record_step("windows_preflight", False, "hermes.exe holds venv")
            sys.exit(2)

        monkeypatch.setattr("hermes_cli.terminal_update.update_source", _fake_impl)
        monkeypatch.setattr(
            hermes_main, "detect_install_method", lambda *a, **k: "git", raising=False
        )
        monkeypatch.setattr(
            hermes_main,
            "_install_hangup_protection",
            lambda gateway_mode: None,
            raising=False,
        )
        monkeypatch.setattr(
            hermes_main, "_finalize_update_output", lambda state: None, raising=False
        )

        class _FakeLock:
            holder = None

            def __init__(self, **_kw):  # cmd_update passes install_root
                pass

            def acquire(self):
                return True

            def release(self):
                pass

        import hermes_cli.update_lock as update_lock_mod

        monkeypatch.setattr(update_lock_mod, "UpdateLock", _FakeLock)

        args = SimpleNamespace(
            check=False, gateway=False, branch=None, yes=False,
            force=False, force_venv=False,
        )
        with pytest.raises(SystemExit) as exc_info:
            hermes_main.cmd_update(args)

        assert exc_info.value.code == 2  # exit code preserved
        latest = ur.read_latest_receipt()
        assert latest is not None
        assert latest["outcome"] == "refused"
        assert latest["exit_code"] == 2
        assert latest["stop_reason"] == "sys.exit(2)"
        assert latest["steps"][0]["name"] == "windows_preflight"
        assert ur.current_correlation_id() is None
        # exactly-once: exactly one receipt file
        directory = receipt_home / "logs" / "update_receipts"
        assert len(list(directory.glob("update_*.json"))) == 1


def _desktop_correlated_receipt(home, correlation):
    """Desktop's managed SSH receipt lookup (managed-ssh-update.ts ``receipt()``)."""
    directory = home / "logs" / "update_receipts"
    for path in sorted(directory.glob("update_*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("correlation_id") != correlation:
            continue
        if payload.get("outcome") == "running" or not payload.get("finished_at"):
            continue
        return payload
    return None


class TestLauncherCorrelation:
    """Desktop matches the remote receipt by HERMES_UPDATE_CORRELATION_ID (#101516)."""

    CORRELATION = "1e7cb007-a6f8-4f6c-badb-d80e204942bf"

    def test_receipt_carries_launcher_correlation(self, receipt_home, monkeypatch):
        monkeypatch.setenv("HERMES_UPDATE_CORRELATION_ID", self.CORRELATION)
        ur.begin_update_receipt()
        update_id = ur.current_correlation_id()
        _finalize("success")

        payload = _desktop_correlated_receipt(receipt_home, self.CORRELATION)
        assert payload is not None
        assert payload["outcome"] == "success"
        # update_id stays the CLI's own id (pm sync receipts bind to it).
        assert payload["update_id"] == update_id != self.CORRELATION

    def test_handoff_receipt_keeps_correlation(self, receipt_home, monkeypatch):
        monkeypatch.setenv("HERMES_UPDATE_CORRELATION_ID", self.CORRELATION)
        ur.begin_update_receipt()
        pre_swap = dict(ur._current.get().data)
        with ur.update_receipt_scope():
            # The post-swap interpreter need not inherit the env var.
            monkeypatch.delenv("HERMES_UPDATE_CORRELATION_ID")
            ur.begin_update_receipt(previous=pre_swap, correlation_id=pre_swap["update_id"])
            _finalize("success")
        assert _desktop_correlated_receipt(receipt_home, self.CORRELATION) is not None

    def test_legacy_handoff_receipt_gains_correlation(self, receipt_home, monkeypatch):
        # A pre-swap receipt from an older updater has no correlation_id field.
        monkeypatch.setenv("HERMES_UPDATE_CORRELATION_ID", self.CORRELATION)
        ur.begin_update_receipt(previous={"update_id": "abc123", "steps": []}, correlation_id="abc123")
        _finalize("success")
        assert _desktop_correlated_receipt(receipt_home, self.CORRELATION)["update_id"] == "abc123"

    def test_no_launcher_correlation_is_null(self, receipt_home, monkeypatch):
        monkeypatch.delenv("HERMES_UPDATE_CORRELATION_ID", raising=False)
        ur.begin_update_receipt()
        _finalize("success")
        assert ur.read_latest_receipt()["correlation_id"] is None






class TestCodeIdentity:

    def test_get_code_identity_cached(self):
        from hermes_cli.build_info import get_code_identity

        first = get_code_identity(refresh=True)
        second = get_code_identity()
        assert first == second
        # returned dicts are copies, not the shared cache
        second["sha"] = "mutated"
        assert get_code_identity()["sha"] == first["sha"]


class TestPreUpdateBackupStep:
    """A deliberate pre-update-backup opt-out is a SKIP carrying its reason; only a backup that
    was requested and captured nothing is a failed step.

    Recording both as ``ok=false, "disabled or failed"`` made a disabled safety net read as a
    broken one in the receipt — the shipped-opt-out case (#94944) looked like a failure for every
    update on the affected machine.
    """

    @staticmethod
    def _record(monkeypatch, *, args, snapshot_id, updates_cfg) -> dict:
        """Run the receipt classifier and return the persisted receipt payload."""
        monkeypatch.setattr(update_cmd_maint, "_load_updates_cfg", lambda: dict(updates_cfg))
        ur.begin_update_receipt()
        update_cmd._record_pre_update_backup_outcome(args, snapshot_id)
        path = _finalize("success")
        assert path is not None and path.is_file()
        return json.loads(path.read_text(encoding="utf-8"))

    @pytest.mark.parametrize(
        "args, updates_cfg, expected_reason",
        [
            # The shipped-template case: legacy ``false`` resolves to mode "off" (see #94944).
            (SimpleNamespace(no_backup=False, backup=False), {"pre_update_backup": False},
             "updates.pre_update_backup"),
            # An explicit flag is a different opt-out and must name itself.
            (SimpleNamespace(no_backup=True, backup=False), {"pre_update_backup": "quick"},
             "--no-backup"),
        ],
    )
    def test_opt_out_records_a_skip_not_a_failed_step(
        self, receipt_home, monkeypatch, args, updates_cfg, expected_reason
    ):
        payload = self._record(monkeypatch, args=args, snapshot_id=None, updates_cfg=updates_cfg)

        step = "pre_update_backup"
        assert [entry for entry in payload["steps"] if entry["name"] == step] == []
        skip = [entry for entry in payload["skips"] if entry["name"] == step]
        assert len(skip) == 1
        assert expected_reason in skip[0]["reason"]

    def test_only_an_opt_out_produces_a_skip(self, receipt_home, monkeypatch):
        """A requested backup never lands in ``skips``: present means captured, absent means failed."""
        args = SimpleNamespace(no_backup=False, backup=False)
        updates_cfg = {"pre_update_backup": "quick"}

        captured = self._record(
            monkeypatch, args=args, snapshot_id="20260911-021847-pre-update", updates_cfg=updates_cfg
        )
        assert captured["skips"] == []
        assert [step["ok"] for step in captured["steps"]] == [True]
        assert captured["steps"][0]["detail"] == "snapshot=20260911-021847-pre-update"

        empty = self._record(monkeypatch, args=args, snapshot_id=None, updates_cfg=updates_cfg)
        assert empty["skips"] == []
        assert [step["ok"] for step in empty["steps"]] == [False]
