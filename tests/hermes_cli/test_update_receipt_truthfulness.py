"""Native update receipt durability and honest outcome classification."""

import json

import pytest

import hermes_cli.update_receipt as ur



@pytest.fixture()
def receipt_home(tmp_path, monkeypatch):
    """Hermetic HERMES_HOME so receipts never touch the real profile."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(
        "hermes_cli.config.get_hermes_home", lambda: home, raising=False
    )
    ur._current.set(None)
    yield home
    ur._current.set(None)


def _receipt_files(home):
    directory = home / "logs" / "update_receipts"
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.json"))


class TestReceiptAlwaysFinalized:
    """Invariant 1 — every finalized run leaves a parseable record; a crash
    leaves NO record the reader could mistake for success (#91283, #92780)."""

    @pytest.mark.parametrize("outcome", ["success", "failed", "refused"])
    def test_finalize_writes_parseable_receipt_for_outcome(
        self, receipt_home, outcome
    ):
        ur.begin_update_receipt()
        ur.record_step("git_pull", outcome == "success", "detail")
        path = ur.finalize_update_receipt(outcome)
        assert path is not None and path.is_file()

        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["outcome"] == outcome
        assert payload["finished_at"] is not None
        assert payload["steps"][0]["name"] == "git_pull"

        latest = ur.read_latest_receipt()
        assert latest is not None
        assert latest["outcome"] == outcome

    def test_nothing_on_disk_until_finalize(self, receipt_home):
        """The receipt is written atomically at finalize — a run that is
        still going (or that dies) has NO on-disk artifact to misread."""
        ur.begin_update_receipt()
        ur.record_step("pre_update_backup", True)
        assert _receipt_files(receipt_home) == []

    def test_crash_without_finalize_never_claims_success(self, receipt_home):
        """Simulated crash: begin + steps, then the process dies (fresh
        module state). The Desktop's reader (#92780 reads the receipt via
        read_latest_receipt) must see NO successful update."""
        ur.begin_update_receipt()
        ur.record_step("git_pull", True)
        ur.record_step("pip_install", True)
        # Crash: module singleton is gone, finalize never ran.
        ur._current.set(None)

        assert _receipt_files(receipt_home) == []
        latest = ur.read_latest_receipt()
        # No receipt at all — the reader cannot report success. If this
        # ever returns a dict, it must not claim a completed success.
        assert latest is None or latest.get("outcome") != "success"

    def test_boundary_safety_net_records_crash_as_not_success(
        self, receipt_home
    ):
        """When the command boundary DOES catch the unwind (#91283), a
        non-zero exit finalizes as failed — never success."""
        ur.begin_update_receipt()
        ur.record_step("git_pull", False, "network died")
        path = ur.finalize_pending_update_receipt(1, "sys.exit(1)")
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["outcome"] == "failed"
        assert ur.read_latest_receipt()["outcome"] == "failed"


class TestRefusalIsNotFailure:
    """Invariant 3 — #91439: refusal (exit 2) and failure are distinct
    outcomes, and the reader reports which one happened."""

    def test_refusal_receipt_distinguishable_from_failure(
        self, receipt_home
    ):
        # Run 1: a real failure.
        ur.begin_update_receipt()
        ur.record_step("git_fetch", False, "remote unreachable")
        failed_path = ur.finalize_pending_update_receipt(1, "sys.exit(1)")
        failed = json.loads(failed_path.read_text(encoding="utf-8"))

        # Run 2: a preflight refusal (venv-holder / concurrent instance).
        ur.begin_update_receipt()
        ur.record_step("venv_preflight", False, "another hermes holds venv")
        refused_path = ur.finalize_pending_update_receipt(2, "sys.exit(2)")
        refused = json.loads(refused_path.read_text(encoding="utf-8"))

        assert failed["outcome"] == "failed" and failed["exit_code"] == 1
        assert refused["outcome"] == "refused" and refused["exit_code"] == 2
        assert refused["outcome"] != failed["outcome"]

        # The reader (Desktop path, #92780/#91439) reports refusal as
        # refusal — not failure, and certainly not success.
        latest = ur.read_latest_receipt()
        assert latest["outcome"] == "refused"
        assert latest["stop_reason"] == "sys.exit(2)"

    def test_refused_receipt_survives_with_its_steps(self, receipt_home):
        """#91439: the refused run's receipt keeps the evidence of WHY —
        the failing preflight step is preserved, not lost."""
        ur.begin_update_receipt()
        ur.record_step("windows_preflight", False, "hermes.exe running")
        path = ur.finalize_pending_update_receipt(2, "concurrent instance")
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["steps"][0]["name"] == "windows_preflight"
        assert payload["steps"][0]["ok"] is False
        assert payload["outcome"] == "refused"
