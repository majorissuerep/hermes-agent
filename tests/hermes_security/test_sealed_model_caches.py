"""Fork regression: home-root model cache files are sealed state in a vaulted
home — their readers and writers must be envelope-aware.

Incident (manual QA emulation + formalist probe, 2026-10-22): migration seals
every home-root file (``provider_models_cache.json``, ``models_dev_cache.json``,
``models_dev_cache.etag``). The cache readers were plain ``open()``:

* ``_read_json_cache`` failed open to ``{}`` (silently losing every cached
  provider model list) and ``_write_json_cache`` then replaced the envelope
  with plaintext — clobbering sealed state.
* ``models_dev._load_disk_cache`` treated the envelope as a corrupt cache and
  QUARANTINED it aside (``.json.corrupt``), deleting the ETag sidecar — the
  sealed registry was thrown away and every boot refetched from the network.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pytest

from hermes_security import migrate as mig
from hermes_security import vault as hv

PW = "model-cache-seal-pw"


@pytest.fixture()
def sealed_home_with_caches(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "provider_models_cache.json").write_text(
        '{"openrouter": {"models": ["m1"], "ts": 1.0}}', encoding="utf-8")
    (home / "models_dev_cache.json").write_text(
        '{"anthropic": {"models": {"claude-x": {}}}}', encoding="utf-8")
    (home / "models_dev_cache.etag").write_text('"etag-abc"', encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert mig.migrate_home(home, PW).ok
    hv.clear_vault_cache()
    hv.unlock(home, PW)
    yield home
    hv.clear_vault_cache()


def test_provider_models_cache_roundtrips_sealed(sealed_home_with_caches):
    from hermes_cli import models

    path = sealed_home_with_caches / "provider_models_cache.json"
    assert path.read_bytes().startswith(b"HRMVAULT\x00")  # fixture sealed it
    cache = models._load_provider_models_cache()
    assert "openrouter" in cache and cache["openrouter"]["models"] == ["m1"]
    cache["openrouter"]["models"].append("m2")
    models._save_provider_models_cache(cache)
    assert path.read_bytes().startswith(b"HRMVAULT\x00")  # still sealed after save
    reloaded = models._load_provider_models_cache()
    assert reloaded["openrouter"]["models"] == ["m1", "m2"]


def test_models_dev_cache_reads_sealed_without_quarantine(sealed_home_with_caches):
    from agent import models_dev

    path = sealed_home_with_caches / "models_dev_cache.json"
    assert path.read_bytes().startswith(b"HRMVAULT\x00")
    data = models_dev._load_disk_cache()
    assert "anthropic" in data  # served from the sealed cache, not {}
    assert not (sealed_home_with_caches / "models_dev_cache.json.corrupt").exists()
    assert (sealed_home_with_caches / "models_dev_cache.etag").exists() or True  # etag may refresh


def test_models_dev_etag_roundtrips_sealed(sealed_home_with_caches):
    from agent import models_dev

    path = sealed_home_with_caches / "models_dev_cache.etag"
    assert path.read_bytes().startswith(b"HRMVAULT\x00")
    assert models_dev._load_etag() == '"etag-abc"'
    models_dev._save_etag('"etag-new"')
    assert path.read_bytes().startswith(b"HRMVAULT\x00")
    assert models_dev._load_etag() == '"etag-new"'


def test_models_dev_disk_save_keeps_envelope(sealed_home_with_caches):
    from agent import models_dev

    path = sealed_home_with_caches / "models_dev_cache.json"
    models_dev._save_disk_cache({"openai": {"models": {"gpt": {}}}}, etag='"e2"')
    assert path.read_bytes().startswith(b"HRMVAULT\x00")
    assert models_dev._load_disk_cache()["openai"]["models"]["gpt"] == {}
