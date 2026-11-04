"""Audit appends keep the migration's transcript-frame format and fail closed."""
from __future__ import annotations

import json

import pytest

from cron import scheduler
from hermes_security import frames, vault
from hermes_security.migrate import migrate_home


@pytest.fixture
def audit(tmp_path, monkeypatch):
    home = tmp_path / "owner"
    path = home / "cron" / "usage_audit.jsonl"
    path.parent.mkdir(parents=True)
    old = {"job_id": "synthetic historical identifier", "error": None}
    path.write_text(json.dumps(old) + "\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(scheduler, "_get_hermes_home", lambda: home)
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    assert migrate_home(home, "synthetic audit password").ok
    vault.unlock(home, "synthetic audit password")
    yield home, path, old
    vault.clear_vault_cache()


def test_migrated_audit_preserves_old_record_and_encrypts_new_append(audit):
    _, path, old = audit
    new = {"job_id": "synthetic private identifier e819", "error": "synthetic private diagnostic"}
    scheduler._write_usage_audit(new)
    assert new["job_id"].encode() not in path.read_bytes()
    rows = [json.loads(payload) for payload in frames.read_frames(path, purpose="transcript")]
    assert rows == [old, new]


@pytest.mark.parametrize("failure", ["locked", "damaged", "plaintext"])
def test_unavailable_audit_is_preserved_and_reports_failed_append(audit, failure, caplog):
    home, path, _ = audit
    if failure == "locked":
        vault.lock_now(home)
    elif failure == "damaged":
        blob = bytearray(path.read_bytes())
        blob[-1] ^= 1
        path.write_bytes(blob)
    else:
        # Legacy plaintext introduced after migration needs explicit repair, never replacement.
        path.write_text('{"job_id":"synthetic legacy identifier"}\n')
    before = path.read_bytes()
    scheduler._write_usage_audit({"job_id": "synthetic forbidden append"})
    assert path.read_bytes() == before
    assert "usage_audit write failed" in caplog.text
