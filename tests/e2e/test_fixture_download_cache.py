"""A shared download cache must not share fixture state or replace existing caches."""
import pytest

from tests.e2e import vault_home


@pytest.mark.platforms("linux", "macos")
def test_fixture_pm_uses_shared_downloads_but_independent_state(tmp_path, monkeypatch):
    from pm.packages import uv_cache_dir

    shared = tmp_path / "downloads"
    shared.mkdir()
    monkeypatch.setenv("UV_CACHE_DIR", str(shared))
    prepared = []

    class Child:
        returncode = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def communicate(self, timeout):
            return "", None

    def spawn(argv, *, env, **kwargs):
        from pathlib import Path

        home = Path(env["HERMES_HOME"])
        with vault_home._fixture_pm_home(home):
            assert uv_cache_dir().resolve() == shared
        prepared.append(home)
        return Child()

    monkeypatch.setattr(vault_home, "spawn_unlocked", spawn)
    monkeypatch.setattr(vault_home, "seal_fixture_home", lambda home: None)
    homes = [tmp_path / "first", tmp_path / "second"]
    for index, home in enumerate(homes):
        home.mkdir()
        (home / "config.yaml").write_text(f"fixture: {index}\n")
        (home / ".env").write_text(f"SYNTHETIC_CREDENTIAL=fixture-{index}\n")
        vault_home._prepare_fixture_pm(home, tmp_path, ("all",))
        vault_home._prepare_fixture_pm(home, tmp_path, ("all",))
        assert not (home / "installs").exists()
        assert not (home / "tools").exists()
    (homes[0] / "config.yaml").write_text("fixture: changed\n")
    assert (homes[1] / "config.yaml").read_text() == "fixture: 1\n"
    assert (homes[1] / ".env").read_text() == "SYNTHETIC_CREDENTIAL=fixture-1\n"
    assert prepared == [homes[0], homes[0], homes[1], homes[1]]


@pytest.mark.platforms("linux", "macos")
def test_fixture_shared_download_cache_refuses_existing_cache(tmp_path, monkeypatch):
    from hermes_constants import get_default_hermes_root

    shared, home = tmp_path / "downloads", tmp_path / "owner"
    shared.mkdir()
    monkeypatch.setenv("UV_CACHE_DIR", str(shared))
    cache = get_default_hermes_root(home=home) / "cache" / "uv"
    cache.mkdir(parents=True)
    sentinel = cache / "owned-download"
    sentinel.write_bytes(b"preserve existing cache")
    monkeypatch.setattr(vault_home, "seal_fixture_home", lambda home: pytest.fail("must refuse before sealing"))
    with pytest.raises(FileExistsError):
        vault_home._prepare_fixture_pm(home, tmp_path, ("all",))
    assert sentinel.read_bytes() == b"preserve existing cache"
    assert not cache.is_symlink()
