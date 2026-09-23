"""gateway.external_platforms kill switch (fork: external-surface lockdown).

Any connection to an external messenger/platform is a vulnerability surface
on this fork. ``gateway.external_platforms: false`` force-disables EVERY
external platform adapter regardless of per-platform config; LOCAL (CLI,
TUI, desktop, dashboard) must keep working.
"""

from gateway.config import GatewayConfig, Platform


def test_lockdown_kills_external_platforms():
    cfg = GatewayConfig.from_dict({
        "external_platforms": False,
        "platforms": {
            "telegram": {"enabled": True, "token": "x"},
            "discord": {"enabled": True, "token": "y"},
            "slack": {"enabled": True, "token": "z"},
        },
    })
    for plat in (Platform.TELEGRAM, Platform.DISCORD, Platform.SLACK):
        assert not cfg.platforms[plat].enabled, f"{plat} must be force-disabled"


def test_lockdown_preserves_local():
    cfg = GatewayConfig.from_dict({
        "external_platforms": False,
        "platforms": {"local": {"enabled": True}, "telegram": {"enabled": True}},
    })
    assert cfg.platforms[Platform.LOCAL].enabled
    assert not cfg.platforms[Platform.TELEGRAM].enabled


def test_default_absent_key_preserves_opt_in():
    """No key = historical per-platform opt-in (back-compat for real users)."""
    cfg = GatewayConfig.from_dict({"platforms": {"telegram": {"enabled": True}}})
    assert cfg.platforms[Platform.TELEGRAM].enabled


def test_lockdown_explicit_true_preserves_opt_in():
    cfg = GatewayConfig.from_dict({
        "external_platforms": True,
        "platforms": {"telegram": {"enabled": True}},
    })
    assert cfg.platforms[Platform.TELEGRAM].enabled


def test_lockdown_wins_over_everything():
    """Even a platform block that would enable via any spelling dies."""
    cfg = GatewayConfig.from_dict({
        "external_platforms": "false",  # string coercion, hostile config
        "platforms": {"telegram": {"enabled": "true", "token": "x"}},
    })
    assert not cfg.platforms[Platform.TELEGRAM].enabled
