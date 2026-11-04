"""Native CLI routing for credentials explicitly owned by the selected profile."""
from __future__ import annotations

import json
from urllib.parse import urlsplit

from hermes_cli.management_config import ManagementError


def codex_api_arguments(env: dict[str, str]) -> list[str]:
    """Select the native OpenAI API route; an absent profile key keeps CLI-owned login intact.

    Codex 0.166 uses ``openai_base_url``, not ``OPENAI_BASE_URL``, for its
    built-in route. No API key is put in these arguments or a new auth file.
    """
    if not env.get("CODEX_API_KEY"):
        return []
    endpoint = env.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    try:
        parts = urlsplit(endpoint)
        valid = (parts.scheme in {"http", "https"} and parts.hostname and parts.username is None
                 and parts.password is None and not parts.query and not parts.fragment
                 and not any(ord(char) < 32 for char in endpoint))
    except ValueError:
        valid = False
    if not valid:
        raise ManagementError("invalid_config", "Codex requires an HTTP API base URL without embedded "
                              "credentials, query or fragment; keep credentials in the profile's secret store")
    values = {"model_provider": "openai", "forced_login_method": "api", "openai_base_url": endpoint}
    return [argument for name, value in values.items() for argument in ("-c", name + "=" + json.dumps(value))]
