"""The dashboard editor shares the CLI's sealed, profile-scoped config path."""

from pathlib import Path

import pytest
import hermes_yaml as yaml


@pytest.mark.asyncio
async def test_encrypted_editor_roundtrip_is_profile_scoped_a_b_a(tmp_path, monkeypatch):
    from hermes_cli.config import DEFAULT_CONFIG
    from hermes_cli.profiles import get_profile_dir
    from hermes_cli.web_models import RawConfigUpdate
    from hermes_cli.web_routers.analytics import get_config_raw, update_config_raw
    from hermes_security import io, vault
    from hermes_security.errors import VaultLockedError

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    homes = {name: get_profile_dir(name) for name in ("a", "b")}
    try:
        for name, home in homes.items():
            vault.init_vault(home, "synthetic-config-editor-password")
            vault.unlock(home, "synthetic-config-editor-password")
            io.write_text(home / "config.yaml", yaml.safe_dump({
                "_config_version": DEFAULT_CONFIG["_config_version"],
                "agent": {"system_prompt": "profile-" + name},
            }), purpose="config")
        for name in ("a", "b", "a"):
            home = homes[name]
            result = await get_config_raw(profile=name)
            assert Path(result["path"]) == home / "config.yaml"
            data = yaml.safe_load(result["yaml"])
            assert data["agent"]["system_prompt"] == "profile-" + name
            data["agent"]["max_turns"] = 37
            await update_config_raw(RawConfigUpdate(yaml_text=yaml.safe_dump(data)), profile=name)
            edited = yaml.safe_load((await get_config_raw(profile=name))["yaml"])
            assert edited["agent"] == data["agent"]
            sealed = (home / "config.yaml").read_bytes()
            assert sealed.startswith(b"HRMVAULT") and b"profile-" not in sealed
        vault.clear_vault_cache()
        with pytest.raises(VaultLockedError):
            await get_config_raw(profile="b")
    finally:
        vault.clear_vault_cache()
