"""Historical platform env-name recognition for existing encrypted config routing.

No setup form or transport consumes this table. Retaining old names prevents
legacy credentials from being accidentally written as ordinary YAML settings.
"""

SETUP_HIDDEN_ENV_SUFFIXES = (
    "_HOME_CHANNEL", "_HOME_CHANNEL_NAME", "_HOME_CHANNEL_THREAD_ID", "_HOME_ADDRESS", "_ALLOW_ALL_USERS",
    "_REPLY_TO_MODE", "_REPLY_MODE", "_REQUIRE_MENTION", "_AUTO_THREAD", "_FREE_RESPONSE_CHANNELS",
    "_FREE_RESPONSE_ROOMS", "_ALLOWED_CHANNELS", "_PROXY",
)


def is_setup_hidden_env(name: str) -> bool:
    """Recognize a historical platform env name without enabling that platform."""
    return name.endswith(SETUP_HIDDEN_ENV_SUFFIXES)
