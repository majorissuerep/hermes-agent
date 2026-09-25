"""Fork kill-switch contract: gateway.external_platforms: false must disable env-enabled platforms.

Incident (luoman-MS73-HB1, 2026-09-25): with the switch set and TELEGRAM_BOT_TOKEN present in the
vaulted .env, the gateway still connected to Telegram ("Connected (polling mode)") and answered
messages. The lockdown in ``GatewayConfig.from_dict`` only saw YAML platform blocks; the
env-enablement pass (``_apply_env_overrides``) force-enables credential-bearing platforms AFTER
it, so the kill switch never fired for the env path.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig


def _config_with_lockdown(**env) -> GatewayConfig:
    """Build a config the way the gateway does: from_dict + env overrides."""
    import os

    from gateway.config_env import _apply_env_overrides

    data = {
        "gateway": {"external_platforms": False},
        "platforms": {},  # no YAML platform blocks — pure env enablement (the incident shape)
    }
    config = GatewayConfig.from_dict(data)
    for key, value in env.items():
        os.environ[key] = value
    try:
        _apply_env_overrides(config)
    finally:
        for key in env:
            os.environ.pop(key, None)
    return config


def test_env_enabled_telegram_is_disabled_by_the_kill_switch(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:incident-token")
    config = _config_with_lockdown(TELEGRAM_BOT_TOKEN="123:incident-token")
    tg = config.platforms.get(Platform.TELEGRAM)
    assert tg is not None, "env enablement must still register the platform (credentials captured)"
    assert tg.enabled is False, "external_platforms: false must beat env-credential enablement"


def test_kill_switch_leaves_local_surfaces_alone():
    config = GatewayConfig.from_dict({
        "gateway": {"external_platforms": False},
        "platforms": {
            "local": {"enabled": True},
            "api_server": {"enabled": True},
            "webhook": {"enabled": True},
        },
    })
    from gateway.config_env import _apply_env_overrides

    _apply_env_overrides(config)
    assert config.platforms[Platform.LOCAL].enabled is True
    assert config.platforms[Platform.API_SERVER].enabled is True
    assert config.platforms[Platform.WEBHOOK].enabled is True


def test_default_true_preserves_env_enablement(monkeypatch):
    """No switch set → the old behavior: token presence enables Telegram."""
    import os

    from gateway.config_env import _apply_env_overrides

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ok-token")
    config = GatewayConfig.from_dict({"platforms": {}})
    _apply_env_overrides(config)
    assert config.platforms[Platform.TELEGRAM].enabled is True


def test_explicit_yaml_block_is_still_disabled(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord-incident-token")
    config = GatewayConfig.from_dict({
        "gateway": {"external_platforms": False},
        "platforms": {"discord": {"enabled": True, "token": "yaml-token"}},
    })
    from gateway.config_env import _apply_env_overrides

    _apply_env_overrides(config)
    assert config.platforms[Platform.DISCORD].enabled is False
