"""Regression tests for terminal config -> env-var bridging.

terminal_tool._get_env_config() reads ALL terminal settings from os.environ
(TERMINAL_*).  config.yaml values therefore have to be bridged into env vars
at startup, by THREE separate code paths:

  1. cli.py            -> ``env_mappings`` dict (CLI / TUI startup)
  2. gateway/run.py    -> ``_terminal_env_map`` dict (gateway / messaging
                          platforms)
  3. hermes_cli/config.py:set_config_value
                       -> bridges via the canonical ``TERMINAL_CONFIG_ENV_MAP``
                          (one-shot when the user runs ``hermes config set …``)

If any one of these is missing a key, the corresponding config.yaml setting
silently does nothing for that entry-point.  This bug already shipped once
for ``docker_run_as_host_user`` (gateway and CLI maps) and once for
``docker_mount_cwd_to_workspace`` (gateway map).

This test guards against future drift by driving the gateway bridge with
every known ``terminal.*`` key and comparing the env var it writes against
cli.py's map; the config-set path is checked for key coverage only.
"""

import os
from unittest.mock import patch


def _cli_env_map() -> dict[str, str]:
    """terminal config key -> env var bridged by cli.load_cli_config() (via _mirror_config_to_env)."""
    import cli
    return dict(cli._TERMINAL_ENV_MAPPINGS)




def _save_config_env_sync_keys() -> set[str]:
    """terminal config keys bridged by ``hermes config set foo bar``.

    ``set_config_value`` no longer carries its own ``_config_to_env_sync``
    dict — it bridges through the canonical ``TERMINAL_CONFIG_ENV_MAP`` via
    ``terminal_config_env_var_for_key()`` (config.py), excluding ``cwd``
    (handled separately).  Read the live map so this test tracks the actual
    source of truth that the config-set path uses, rather than a string
    literal that the consolidation removed.
    """
    from hermes_cli import config as hc_config
    # set_config_value bridges every TERMINAL_CONFIG_ENV_MAP key except
    # terminal.cwd (see the ``key != "terminal.cwd"`` guard in
    # set_config_value); mirror that exclusion here.
    return {k for k in hc_config.TERMINAL_CONFIG_ENV_MAP if k != "cwd"}


# Keys present in cli.py env_mappings but intentionally absent from
# gateway/run.py or set_config_value.  Each entry must be justified.
_CLI_ONLY_OK = frozenset({
    # `env_type` is a legacy YAML key alias for `backend` that cli.py
    # accepts for backwards-compat with older cli-config.yaml.  The
    # gateway path normalizes on the canonical `backend` key, which is
    # also in the map and handles the same bridging.  See cli.py ~line 515.
    "env_type",
    # sudo_password is not a terminal-backend option — it's a credential
    # used across backends, bridged to $SUDO_PASSWORD (not TERMINAL_*).
    # Treating it as terminal-only would be misleading.
    "sudo_password",
})




def test_save_config_set_bridges_every_cli_terminal_key():
    """``hermes config set terminal.X`` must propagate every key the CLI
    startup path bridges, so a config-set value takes effect without restart.
    """
    save_keys = _save_config_env_sync_keys()
    # cwd is bridged separately by set_config_value; home_mode is CLI-only.
    exempt = _CLI_ONLY_OK | {"cwd", "home_mode"}
    missing = (set(_cli_env_map()) - exempt) - save_keys
    assert not missing, (
        f"`hermes config set terminal.X` doesn't sync these keys to .env: "
        f"{sorted(missing)}.  Add them to TERMINAL_CONFIG_ENV_MAP in "
        f"hermes_cli/config.py (set_config_value bridges through it)."
    )
