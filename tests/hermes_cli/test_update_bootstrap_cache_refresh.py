"""Mutable POSIX installer caches heal without changing pins or sibling refs."""
from pathlib import Path

import pytest
from hermes_cli import main as cli_main


@pytest.mark.parametrize("ref", ["main", "feature/gui", "a1b2c3d", "a" * 40])
def test_only_the_selected_mutable_posix_cache_is_repaired(tmp_path, monkeypatch, ref):
    home, source = tmp_path / "home", tmp_path / "source"
    scripts, cache = source / "scripts", home / "bootstrap-cache"
    scripts.mkdir(parents=True)
    cache.mkdir(parents=True)
    (scripts / "install.sh").write_bytes(b"current installer")
    selected = cache / ("install-" + ref.replace("/", "_") + ".sh")
    selected.write_bytes(b"old installer")
    sibling = cache / "install-other.sh"
    sibling.write_bytes(b"other branch")
    monkeypatch.setattr(cli_main, "PROJECT_ROOT", source)
    monkeypatch.setattr(cli_main, "get_hermes_home", lambda: home)
    cli_main._refresh_bootstrap_cache_scripts(ref)
    pinned = all(c in "0123456789abcdef" for c in ref) and 7 <= len(ref) <= 40
    assert selected.read_bytes() == (b"old installer" if pinned else (scripts / "install.sh").read_bytes())
    assert sibling.read_bytes() == b"other branch"
    assert not list(cache.glob("*.tmp"))


def test_cache_refresh_does_not_create_unrequested_files(tmp_path, monkeypatch):
    home, source = tmp_path / "home", tmp_path / "source"
    monkeypatch.setattr(cli_main, "PROJECT_ROOT", source)
    monkeypatch.setattr(cli_main, "get_hermes_home", lambda: home)
    cli_main._refresh_bootstrap_cache_scripts("main")
    assert not home.exists() and not source.exists()
