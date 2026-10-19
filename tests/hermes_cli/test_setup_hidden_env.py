"""Historical platform secret-name recognition remains a static config-routing denylist."""

from hermes_cli.setup_hidden_env import is_setup_hidden_env


class TestIsSetupHiddenEnv:


    def test_applies_to_plugin_platforms_nobody_enumerated(self):
        """Suffix matching is the point — IRC/SimpleX/ntfy get this for free."""
        for key in (
            "IRC_ALLOW_ALL_USERS",
            "SIMPLEX_HOME_CHANNEL",
            "NTFY_HOME_CHANNEL_NAME",
            "LINE_ALLOW_ALL_USERS",
        ):
            assert is_setup_hidden_env(key), key
