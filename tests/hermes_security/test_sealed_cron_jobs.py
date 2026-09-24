"""Fork regression: the cron jobs store is sealed state in a vaulted home.

Incident (luoman-MS73-HB1, 2026-09-25): migration sealed ``cron/jobs.json``; ``_parse_jobs_file``
opened it with a plain ``open()``, hit ciphertext, and every scheduler tick died with
"Cron database corrupted and unrepairable". ``_save_jobs_unlocked`` wrote plaintext, which would
clobber the envelope.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pytest

from hermes_security import migrate as mig
from hermes_security import vault as hv

PW = "sealed-cron-pw"


@pytest.fixture()
def sealed_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    (home / "cron").mkdir(parents=True)
    (home / "cron" / "jobs.json").write_text(
        '{"jobs": [{"id": "j1", "name": "daily", "schedule": "1d"}], "updated_at": "2026-09-25T00:00:00"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert mig.migrate_home(home, PW).ok
    hv.clear_vault_cache()
    hv.unlock(home, PW)
    yield home
    hv.clear_vault_cache()


def test_parse_jobs_file_reads_the_sealed_store(sealed_home):
    from cron import jobs as cron_jobs

    path = sealed_home / "cron" / "jobs.json"
    assert path.read_bytes().startswith(b"HRMVAULT\x00")  # fixture must seal it
    data, strict = cron_jobs._parse_jobs_file(path)
    assert strict is False
    assert [j["id"] for j in data["jobs"]] == ["j1"]


def test_save_jobs_roundtrips_through_the_envelope(sealed_home):
    from cron import jobs as cron_jobs

    path = sealed_home / "cron" / "jobs.json"
    cron_jobs.save_jobs([{"id": "j2", "name": "hourly", "schedule": "1h"}], replace=True)
    assert path.read_bytes().startswith(b"HRMVAULT\x00")  # still sealed after save
    loaded = cron_jobs.load_jobs()
    assert [j["id"] for j in loaded] == ["j2"]
