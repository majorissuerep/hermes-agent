"""Tests for hermes_cli.model_catalog — remote manifest fetch + cache + fallback."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Isolate HERMES_HOME + reset any module-level catalog cache per test."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))

    # Force a fresh catalog module state for each test.
    import importlib
    from hermes_cli import model_catalog
    importlib.reload(model_catalog)
    yield home
    model_catalog.reset_cache()


def _valid_manifest() -> dict:
    return {
        "version": 1,
        "updated_at": "2026-04-25T22:00:00Z",
        "metadata": {"source": "test"},
        "providers": {
            "openrouter": {
                "metadata": {"display_name": "OpenRouter"},
                "models": [
                    {"id": "anthropic/claude-opus-4.7", "description": "recommended"},
                    {"id": "openai/gpt-5.4", "description": ""},
                    {"id": "openrouter/elephant-alpha", "description": "free"},
                ],
            },
            "nous": {
                "metadata": {"display_name": "Nous Portal"},
                "models": [
                    {"id": "anthropic/claude-opus-4.7"},
                    {"id": "moonshotai/kimi-k2.6"},
                ],
            },
        },
    }


class TestValidation:
    def test_accepts_well_formed_manifest(self, isolated_home):
        from hermes_cli.model_catalog import _validate_manifest
        assert _validate_manifest(_valid_manifest()) is True

    def test_rejects_non_dict(self, isolated_home):
        from hermes_cli.model_catalog import _validate_manifest
        assert _validate_manifest("string") is False
        assert _validate_manifest([]) is False
        assert _validate_manifest(None) is False


    def test_rejects_non_string_model_id(self, isolated_home):
        from hermes_cli.model_catalog import _validate_manifest
        m = _valid_manifest()
        m["providers"]["openrouter"]["models"][0] = {"id": 42}
        assert _validate_manifest(m) is False


class TestFetchSuccess:
    def test_fetch_and_cache_writes_disk(self, isolated_home):
        from hermes_cli import model_catalog
        manifest = _valid_manifest()
        with patch.object(
            model_catalog, "_fetch_manifest", return_value=manifest
        ) as fetch:
            result = model_catalog.get_catalog(force_refresh=True)

        assert result == manifest
        assert fetch.called

        cache_file = model_catalog._cache_path()
        assert cache_file.exists()
        with open(cache_file) as fh:
            assert json.load(fh) == manifest


class TestFetchFailure:
    def test_network_failure_returns_empty_when_no_cache(self, isolated_home):
        from hermes_cli import model_catalog
        with patch.object(model_catalog, "_fetch_manifest", return_value=None):
            result = model_catalog.get_catalog(force_refresh=True)
        assert result == {}

    def test_network_failure_falls_back_to_disk_cache(self, isolated_home):
        from hermes_cli import model_catalog
        # Prime disk cache with a fresh copy.
        manifest = _valid_manifest()
        with patch.object(model_catalog, "_fetch_manifest", return_value=manifest):
            model_catalog.get_catalog(force_refresh=True)

        # Now wipe in-process cache and simulate network failure on refetch.
        model_catalog.reset_cache()
        with patch.object(model_catalog, "_fetch_manifest", return_value=None):
            result = model_catalog.get_catalog(force_refresh=True)

        assert result == manifest

    def test_fetch_failure_falls_back_to_stale_cache(self, isolated_home):
        from hermes_cli import model_catalog
        manifest = _valid_manifest()
        # Write stale cache directly (mtime in the past).
        cache = model_catalog._cache_path()
        cache.parent.mkdir(parents=True, exist_ok=True)
        with open(cache, "w") as fh:
            json.dump(manifest, fh)
        old = time.time() - 30 * 24 * 3600  # 30 days ago
        import os as _os
        _os.utime(cache, (old, old))

        with patch.object(model_catalog, "_fetch_manifest", return_value=None):
            result = model_catalog.get_catalog()

        # Stale cache is better than nothing.
        assert result == manifest


class TestFallbackChain:
    """``_fetch_manifest_with_fallback`` walks ``DEFAULT_CATALOG_FALLBACK_URLS``
    when the primary URL fails. Regression: the Docusaurus site behind Vercel
    occasionally returns HTTP 403 + x-vercel-mitigated: challenge for urllib;
    without a fallback URL the user's disk cache freezes and new model
    releases (opus 4.8, etc.) never reach the picker.
    """

    PRIMARY = "https://hermes-agent.nousresearch.com/docs/api/model-catalog.json"
    FALLBACK = (
        "https://raw.githubusercontent.com/NousResearch/hermes-agent"
        "/main/website/static/api/model-catalog.json"
    )

    def test_uses_primary_when_it_succeeds(self, isolated_home):
        from hermes_cli import model_catalog
        calls: list[str] = []

        def fake_fetch(url, timeout):
            calls.append(url)
            return _valid_manifest()

        with patch.object(model_catalog, "_fetch_manifest", side_effect=fake_fetch):
            result = model_catalog._fetch_manifest_with_fallback(self.PRIMARY, 5.0)

        assert result is not None
        assert calls == [self.PRIMARY], "fallback URLs must not be touched on primary success"

    def test_falls_through_to_raw_github_on_primary_failure(self, isolated_home):
        from hermes_cli import model_catalog
        calls: list[str] = []

        def fake_fetch(url, timeout):
            calls.append(url)
            if url == self.PRIMARY:
                return None  # simulate Vercel 403
            return _valid_manifest()

        with patch.object(model_catalog, "_fetch_manifest", side_effect=fake_fetch):
            result = model_catalog._fetch_manifest_with_fallback(self.PRIMARY, 5.0)

        assert result is not None
        assert calls == [self.PRIMARY, self.FALLBACK]


    def test_get_catalog_uses_fallback_chain(self, isolated_home):
        """End-to-end: ``get_catalog`` routes through the fallback helper so
        a primary URL failure transparently produces a working catalog."""
        from hermes_cli import model_catalog
        manifest = _valid_manifest()
        calls: list[str] = []

        def fake_fetch(url, timeout):
            calls.append(url)
            if url == self.PRIMARY:
                return None
            return manifest

        with patch.object(model_catalog, "_fetch_manifest", side_effect=fake_fetch):
            result = model_catalog.get_catalog(force_refresh=True)

        assert result == manifest
        assert self.FALLBACK in calls


class TestCuratedAccessors:
    def test_openrouter_returns_tuples(self, isolated_home):
        from hermes_cli import model_catalog
        with patch.object(
            model_catalog, "_fetch_manifest", return_value=_valid_manifest()
        ):
            result = model_catalog.get_curated_openrouter_models()
        assert result == [
            ("anthropic/claude-opus-4.7", "recommended"),
            ("openai/gpt-5.4", ""),
            ("openrouter/elephant-alpha", "free"),
        ]


    def test_nous_returns_none_when_catalog_empty(self, isolated_home):
        from hermes_cli import model_catalog
        with patch.object(model_catalog, "_fetch_manifest", return_value=None):
            assert model_catalog.get_curated_nous_models() is None


class TestDefaultModelFromCache:
    """get_default_model_from_cache reads the '"default": true' label without
    ever hitting the network."""

    def _manifest_with_default(self) -> dict:
        m = _valid_manifest()
        m["providers"]["openrouter"]["models"][1]["default"] = True  # gpt-5.4
        m["providers"]["nous"]["models"][1]["default"] = True  # kimi-k2.6
        return m

    def test_reads_label_from_disk_cache(self, isolated_home):
        from hermes_cli import model_catalog
        cache = isolated_home / "cache"
        cache.mkdir()
        (cache / "model_catalog.json").write_text(
            json.dumps(self._manifest_with_default())
        )
        with patch.object(model_catalog, "_fetch_manifest") as fetch:
            assert (
                model_catalog.get_default_model_from_cache("openrouter")
                == "openai/gpt-5.4"
            )
            assert (
                model_catalog.get_default_model_from_cache("nous")
                == "moonshotai/kimi-k2.6"
            )
            fetch.assert_not_called()

    def test_no_label_returns_none(self, isolated_home):
        from hermes_cli import model_catalog
        cache = isolated_home / "cache"
        cache.mkdir()
        (cache / "model_catalog.json").write_text(json.dumps(_valid_manifest()))
        with patch.object(model_catalog, "_fetch_manifest") as fetch:
            assert model_catalog.get_default_model_from_cache("openrouter") is None
            fetch.assert_not_called()




class TestProviderOverride:
    def test_override_url_takes_precedence(self, isolated_home):
        from hermes_cli import model_catalog

        override_payload = {
            "version": 1,
            "providers": {
                "openrouter": {
                    "models": [
                        {"id": "override/model", "description": "custom"},
                    ]
                }
            },
        }

        def fake_fetch(url, timeout):
            if "override" in url:
                return override_payload
            return _valid_manifest()

        with patch.object(
            model_catalog,
            "_load_catalog_config",
            return_value={
                "enabled": True,
                "url": "http://master",
                "ttl_hours": 24.0,
                "providers": {"openrouter": {"url": "http://override"}},
            },
        ):
            with patch.object(model_catalog, "_fetch_manifest", side_effect=fake_fetch):
                result = model_catalog.get_curated_openrouter_models()

        assert result == [("override/model", "custom")]


class TestRefreshCadence:
    def test_default_ttl_is_twenty_minutes_and_legacy_hours_honoured(self):
        from hermes_cli import model_catalog

        with patch("hermes_cli.config.load_config", return_value={"model_catalog": {"ttl_minutes": 20}}):
            assert model_catalog.refresh_interval_seconds() == 20 * 60
        # A user-set legacy ttl_hours still wins while ttl_minutes sits at its default.
        with patch("hermes_cli.config.load_config", return_value={"model_catalog": {"ttl_minutes": 20, "ttl_hours": 3}}):
            assert model_catalog.refresh_interval_seconds() == 3 * 3600

    def test_refresh_catalogs_forces_every_source(self):
        from hermes_cli import model_catalog

        with patch.object(model_catalog, "_load_catalog_config", return_value={
            "enabled": True, "url": "http://master", "ttl_hours": 1.0, "providers": {},
        }), patch.object(model_catalog, "get_catalog", return_value=_valid_manifest()) as gc, \
             patch("hermes_cli.models.fetch_openrouter_models") as orm, \
             patch("hermes_cli.models.fetch_nous_recommended_models") as nous:
            assert model_catalog.refresh_catalogs() is True
        gc.assert_called_once_with(force_refresh=True)
        orm.assert_called_once_with(force_refresh=True)
        nous.assert_called_once_with(force_refresh=True)


class TestIntegrationWithModelsModule:
    """Exercise the fallback paths via the real callers in hermes_cli.models."""




    def test_picker_max_models_cap_semantics(self, monkeypatch):
        """None, zero and a positive cap retain distinct semantics on the real picker path."""
        from hermes_cli import models
        from hermes_cli.model_switch import list_authenticated_providers
        from hermes_cli.model_switch_providers import list_picker_providers

        expected = ["fixture-one", "fixture-two", "fixture-three"]
        monkeypatch.setenv("AI_GATEWAY_API_KEY", "synthetic-picker-key")
        monkeypatch.setattr("agent.models_dev.fetch_models_dev", dict)
        monkeypatch.setitem(models._PROVIDER_MODELS, "ai-gateway", expected)
        monkeypatch.setattr(models, "cached_provider_model_ids", lambda *_a, **_k: expected)
        full = list_picker_providers(current_provider="ai-gateway", max_models=None)
        one = list_picker_providers(current_provider="ai-gateway", max_models=1)
        zero = list_authenticated_providers(current_provider="ai-gateway", max_models=0)

        def row(rows):
            return next(r for r in rows if r["slug"] == "ai-gateway")

        assert row(full)["models"] == expected
        assert row(one)["models"] == expected[:1]
        assert row(zero)["models"] == []
        assert row(zero)["total_models"] == len(expected)


# -----------------------------------------------------------------------------
# Drift guard — prevent the in-repo curated lists from going out of sync with
# the docs-hosted manifest at website/static/api/model-catalog.json.
#
# History: qwen/qwen3.6-plus was added to _PROVIDER_MODELS["nous"] in commit
# 9dd6e5510 but website/static/api/model-catalog.json was not regenerated for
# weeks, so free-tier users on a new install fetched a stale manifest and the
# free-tier picker showed "No free models currently available." even though
# the Portal was serving qwen/qwen3.6-plus as free. CI must catch this.
# -----------------------------------------------------------------------------


class TestSwrRefreshProfileScope:
    """Two profile homes — A (process default) and B (routed via the HERMES_HOME ContextVar, as
    tui_runtime ``_profile_scoped`` does). The off-thread stale-while-revalidate refresh spawned
    under B must write B's cache file, and A's in-flight refresh must not suppress B's."""

    _CFG = {"enabled": True, "url": "http://master", "ttl_hours": 1.0, "providers": {}}

    @staticmethod
    def _seed_expired(home: Path, manifest: dict) -> Path:
        path = home / "cache" / "model_catalog.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest), encoding="utf-8")
        os.utime(path, (1, 1))  # expired → served stale, refreshed off-thread
        return path

    @staticmethod
    def _join_swr_threads() -> None:
        for t in threading.enumerate():
            if t.name == "model-catalog-swr":
                t.join(5)

    def test_refresh_under_profile_override_writes_that_profiles_cache(self, isolated_home, tmp_path):
        from agent.secret_scope import set_multiplex_active
        from hermes_cli import model_catalog
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override

        old = _valid_manifest()
        fresh = {**_valid_manifest(), "updated_at": "2026-05-01T00:00:00Z"}
        path_a = self._seed_expired(isolated_home, old)
        path_b = self._seed_expired(tmp_path / "profile-b", old)
        a_before = path_a.read_bytes()
        release = threading.Event()

        def fetch(*_args, **_kwargs):
            assert release.wait(5), "caller did not release the refresh"
            return fresh

        set_multiplex_active(True)
        try:
            with patch.object(model_catalog, "_load_catalog_config", return_value=self._CFG), \
                 patch.object(model_catalog, "_fetch_manifest_with_fallback", fetch):
                token = set_hermes_home_override(path_b.parent.parent)
                try:
                    assert model_catalog.get_catalog() == old  # stale copy served without blocking
                finally:
                    reset_hermes_home_override(token)  # the handler returns before the fetch completes
                release.set()
                self._join_swr_threads()
        finally:
            set_multiplex_active(False)

        assert json.loads(path_b.read_text(encoding="utf-8")) == fresh
        assert path_a.read_bytes() == a_before

    def test_inflight_refresh_for_one_profile_does_not_suppress_another(self, isolated_home, tmp_path):
        from agent.secret_scope import set_multiplex_active
        from hermes_cli import model_catalog
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override

        old = _valid_manifest()
        path_a = self._seed_expired(isolated_home, old)
        path_b = self._seed_expired(tmp_path / "profile-b", old)
        release = threading.Event()
        refreshed_paths: list[str] = []
        seen = threading.Lock()

        def fetch(*_args, **_kwargs):
            with seen:
                refreshed_paths.append(str(model_catalog._cache_path()))
            release.wait(5)

        set_multiplex_active(True)
        try:
            with patch.object(model_catalog, "_load_catalog_config", return_value=self._CFG), \
                 patch.object(model_catalog, "_fetch_manifest_with_fallback", fetch):
                model_catalog.get_catalog()  # A's refresh is now in flight (blocked on `release`)
                token = set_hermes_home_override(path_b.parent.parent)
                try:
                    model_catalog.get_catalog()  # B must get its own refresh
                finally:
                    reset_hermes_home_override(token)
                release.set()
                self._join_swr_threads()
        finally:
            set_multiplex_active(False)

        assert sorted(refreshed_paths) == sorted([str(path_a), str(path_b)])


class TestManifestMatchesInRepoLists:
    """Fail if the on-disk manifest is out of date relative to in-repo lists."""

    @staticmethod
    def _strip_volatile(catalog: dict) -> dict:
        """Drop fields that always change (timestamps) for diff comparison."""
        out = dict(catalog)
        out.pop("updated_at", None)
        return out

    def test_in_repo_lists_match_manifest(self):
        """``scripts/build_model_catalog.py`` output must match the committed file.

        If this fails, run ``python scripts/build_model_catalog.py`` and
        commit the regenerated ``website/static/api/model-catalog.json``.
        """
        # Resolve the repo root from this test file's location.
        repo_root = Path(__file__).resolve().parents[2]
        manifest_path = repo_root / "website" / "static" / "api" / "model-catalog.json"

        if not manifest_path.exists():
            pytest.skip(f"manifest missing at {manifest_path}")

        # Build expected catalog using the same script CI would.
        import importlib.util
        script_path = repo_root / "scripts" / "build_model_catalog.py"
        spec = importlib.util.spec_from_file_location("_build_model_catalog", script_path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        expected = mod.build_catalog()

        with open(manifest_path, encoding="utf-8") as fh:
            actual = json.load(fh)

        assert self._strip_volatile(actual) == self._strip_volatile(expected), (
            "website/static/api/model-catalog.json is out of sync with "
            "_PROVIDER_MODELS['nous'] / OPENROUTER_MODELS. "
            "Run: python scripts/build_model_catalog.py && "
            "git add website/static/api/model-catalog.json"
        )
