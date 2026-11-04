"""Bounded real-process management cohort; original assertions and fixtures retained."""

from __future__ import annotations
import base64
import secrets
import pytest
from tests.hermes_cli.test_management_public_cli import (ROOT)

pytest_plugins = ["tests.hermes_cli.test_management_public_cli"]
pytestmark = [pytest.mark.platforms("linux", "macos"), pytest.mark.live_system_guard_bypass]


@pytest.mark.parametrize("global_profile", [False, True], ids=["manage-profile", "global-profile"])
def test_external_keyfile_management_honors_the_selected_profile(world, tmp_path, global_profile):
    from hermes_cli.config import save_config
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_security import vault
    from tests.e2e.vault_home import prepare_fixture_runtime

    private, public = vault.generate_keypair()
    vault.add_key_slot(world.home, public_key_raw=public, unlocked_vault=vault.get_vault(world.home))
    keyfile = tmp_path / "external-agent-key"
    keyfile.write_bytes(base64.b64encode(private))
    keyfile.chmod(0o600)
    external = dict(world.env, HERMES_VAULT_PRIVATE_KEY=str(keyfile))
    external.pop("HERMES_SESSION_VAULT", None)
    result, output = world.call({"op": "config.get", "path": ["agent", "max_turns"]}, env=external)
    assert result.returncode == 0 and output["result"]["value"] == 19
    profile_home = world.home / "profiles" / "work"
    profile_password = secrets.token_urlsafe(32)
    vault.init_vault(profile_home, profile_password)
    owner = vault.unlock(profile_home, profile_password)
    vault.add_key_slot(profile_home, public_key_raw=public, unlocked_vault=owner)
    token = set_hermes_home_override(profile_home)
    try:
        save_config({"agent": {"max_turns": 61}, "updates": {"check": False}}, strip_defaults=False)
        prepare_fixture_runtime(profile_home, ROOT)
    finally:
        reset_hermes_home_override(token)
    result, output = world.call({"op": "config.get", "path": ["agent", "max_turns"]},
                                env=external, profile="work", global_profile=global_profile)
    assert result.returncode == 0 and output["result"]["value"] == 61
    assert world.get(["agent", "max_turns"])["value"] == 19
    result, output = world.call({"op": "config.get", "path": []}, profile="work", global_profile=global_profile)
    assert result.returncode != 0 and output["ok"] is False
