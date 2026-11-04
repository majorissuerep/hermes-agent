"""Bounded real-process management cohort; original assertions and fixtures retained."""

from __future__ import annotations
import json
import secrets
import pytest
from tests.hermes_cli.test_management_public_cli import (_fresh_unlock)

pytest_plugins = ["tests.hermes_cli.test_management_public_cli"]
pytestmark = [pytest.mark.platforms("linux", "macos"), pytest.mark.live_system_guard_bypass]


def test_public_management_rotation_keeps_live_children_and_ciphertext(world):
    from hermes_security import vault
    from hermes_security.session_authority import start_session_authority
    from hermes_state import SessionDB

    before = world.get()
    result, output = world.call({"op": "config.batch", "revision": before["revision"], "operations": [
        {"op": "config.set", "path": ["agent", "max_turns"], "value": 37},
        {"op": "config.set", "path": ["skills", "config", "literal.key"],
         "value": {"enabled": True, "values": [1, None, "kept"]}},
    ]})
    assert result.returncode == 0, (output, result.stderr)
    assert world.get(["agent", "max_turns"])["value"] == 37
    assert world.get(["skills", "config", "literal.key"])["value"]["values"] == [1, None, "kept"]
    secret = "synthetic-private-header-" + secrets.token_hex(16)
    result, output = world.call({"op": "secret.set", "name": "MANAGEMENT_TEST_KEY"}, secret=secret)
    assert result.returncode == 0, (output, result.stderr)
    assert secret not in result.stdout + result.stderr
    revision = world.get()["revision"]
    result, output = world.call({"op": "config.set", "revision": revision,
        "path": ["providers", "custom.route", "extra_headers", "Authorization"],
        "value": "Bearer ${MANAGEMENT_TEST_KEY}"})
    assert result.returncode == 0, (output, result.stderr)
    assert secret not in json.dumps(world.get())
    private, public = vault.generate_keypair()
    vault.add_key_slot(world.home, public_key_raw=public, unlocked_vault=vault.get_vault(world.home))
    unchanged = {name: (world.home / name).read_bytes() for name in ("config.yaml", ".env", "state.db")}
    new_password = secrets.token_urlsafe(32)
    result, output = world.call({"op": "passphrase.change"}, secret=new_password)
    assert result.returncode != 0 and output["error"]["code"] == "consent_required", (output, result.stderr)
    from hermes_security.passphrase import change_passphrase
    change_passphrase(world.home, new_password)  # explicit owning-human action, never the borrowed child
    assert new_password not in result.stdout + result.stderr
    assert unchanged == {name: (world.home / name).read_bytes() for name in unchanged}
    assert world.get(["agent", "max_turns"])["value"] == 37
    child = start_session_authority(world.home, "management-child")
    try:
        child_env = dict(world.env, **child.environment(), HERMES_SESSION_ID="management-child")
        result, output = world.call({"op": "config.get", "path": ["agent", "max_turns"]}, env=child_env)
        assert result.returncode == 0 and output["result"]["value"] == 37
    finally:
        child.close()
    assert world.get(["agent", "max_turns"])["value"] == 37
    assert _fresh_unlock(world, world.password).returncode != 0
    assert _fresh_unlock(world, new_password).returncode == 0
    assert _fresh_unlock(world, private, private_key=True).returncode == 0
    with SessionDB(world.home / "state.db") as db:
        assert db.get_messages("management-history")[0]["content"] == "preserve this encrypted conversation"
    text = vault.get_vault(world.home).read_bytes(world.home / "config.yaml", purpose="config")
    assert b"# Keep this operator comment." in text
    assert (world.home.stat().st_mode & 0o777) == 0o700
    for name in unchanged:
        assert (world.home / name).stat().st_mode & 0o077 == 0
        assert secret.encode() not in (world.home / name).read_bytes()
    world.authority.close()
    result, output = world.call({"op": "config.get", "path": []})
    assert result.returncode != 0 and output["ok"] is False
