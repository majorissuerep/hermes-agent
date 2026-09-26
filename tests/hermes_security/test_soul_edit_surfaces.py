"""Identity editing surfaces must preserve envelope semantics, including profile publish."""
import asyncio
from pathlib import Path

import pytest

from hermes_security import io, vault


@pytest.mark.parametrize("surface", ["web", "rpc"])
def test_profile_soul_editors_preserve_encryption_a_b_a(tmp_path, monkeypatch, surface):
    from hermes_cli import profiles
    from hermes_cli.web_routers import profiles as web
    import tui_gateway.server as server

    homes = {name: tmp_path / name for name in ("alpha", "beta")}
    for name, home in homes.items():
        vault.init_vault(home, "synthetic-soul-edit")
        vault.unlock(home, "synthetic-soul-edit")
        io.write_text(home / "config.yaml", "{}", purpose="config")
        io.write_text(home / "SOUL.md", name, purpose="state")
    monkeypatch.setattr(profiles, "get_profile_dir", lambda name: homes[name])
    monkeypatch.setattr(web, "_resolve_profile_dir", lambda name: homes[name])
    try:
        for index, name in enumerate(("alpha", "beta", "alpha")):
            home = homes[name]
            expected = io.read_text(home / "SOUL.md", purpose="state")
            if surface == "web":
                assert asyncio.run(web.get_profile_soul(name))["content"] == expected
                result = asyncio.run(web.update_profile_soul(name, web.ProfileSoulUpdate(content=f"edit {index}")))
                assert result["ok"]
            else:
                result = server._methods["profiles.describe"](1, {"name": name})
                assert result["result"]["soul"] == expected, result
                result = server._methods["profiles.configure"](2, {"name": name, "soul": f"edit {index}"})
                assert result["result"]["applied"]["soul"], result
            assert io.looks_like_envelope(home / "SOUL.md")
            assert io.read_text(home / "SOUL.md", purpose="state") == f"edit {index}"
    finally:
        vault.clear_vault_cache()


def test_new_profile_soul_is_sealed_for_published_path(tmp_path, monkeypatch):
    from hermes_cli import profiles
    from hermes_cli.default_soul import DEFAULT_SOUL_MD
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(root))
    vault.init_vault(root, "synthetic-profile-create")
    vault.unlock(root, "synthetic-profile-create")
    try:
        created = profiles.create_profile("sealed-child", no_skills=True, no_alias=True)
        assert io.looks_like_envelope(created / "SOUL.md")
        assert io.read_text(created / "SOUL.md", purpose="state") == DEFAULT_SOUL_MD
    finally:
        vault.clear_vault_cache()
