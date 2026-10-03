"""PM and update receipts replace the retired home-root npm/build stamps."""

from hermes_cli import update_receipt
from hermes_security import io, migrate, vault
from pm import receipt


def test_worker_receipts_publish_in_the_owning_vault_and_locked_updates_leave_no_plaintext(
    tmp_path, monkeypatch
):
    homes = [tmp_path / "a", tmp_path / "b"]
    for home in homes:
        home.mkdir()
        vault.init_vault(home, "receipt-test-password")
        vault.unlock(home, "receipt-test-password")
    try:
        for index, home in enumerate([*homes, homes[0]]):
            monkeypatch.setenv("HERMES_HOME", str(home))
            secret = f"private-rebuild-detail-{index}"
            with receipt.worker_context(None):
                receipt.begin("sync")
                receipt.record_step("rebuild", True, secret)
                assert receipt.finalize("ok") is None
                data = receipt.last_completed()
            receipt.accept_worker_receipt(data, None)
            point = home / "logs/update_receipts/latest.json"
            assert point.read_bytes().startswith(b"HRMVAULT")
            assert secret.encode() not in point.read_bytes()
            assert receipt.latest()["steps"][0]["detail"] == secret
            assert update_receipt.read_latest_receipt() == receipt.latest()
            update_receipt.begin_update_receipt()
            update_receipt.record_fact("private_detail", secret)
            archived = update_receipt.finalize_update_receipt("success")
            assert archived is not None
            assert io.read_json(archived, purpose="state")["private_detail"] == secret
            assert secret.encode() not in archived.read_bytes()
            before = point.read_bytes()
            vault.lock_now(home)
            receipt.begin("sync")
            receipt.record_step("rebuild", False, "must-not-leak")
            assert receipt.finalize("failed") is None
            assert point.read_bytes() == before
            assert receipt.latest() is None
            vault.unlock(home, "receipt-test-password")
    finally:
        vault.clear_vault_cache()


def test_migration_preserves_pm_products_but_seals_same_named_user_directories(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    artifact = home / "installs/release/facts.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text('{"public": true}', encoding="utf-8")
    private = home / "documents/installs/private.json"
    private.parent.mkdir(parents=True)
    private.write_text('{"secret": "private-install-notes"}', encoding="utf-8")
    report = migrate.migrate_home(home, "receipt-test-password")
    assert report.ok
    vault.unlock(home, "receipt-test-password")
    try:
        assert artifact.read_text(encoding="utf-8") == '{"public": true}'
        assert b"private-install-notes" not in private.read_bytes()
        assert io.read_json(private, purpose="state")["secret"] == "private-install-notes"
    finally:
        vault.clear_vault_cache()
