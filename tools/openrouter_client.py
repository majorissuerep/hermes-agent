"""OpenRouter API key probe shared by Hermes tools."""

import os


def resolve_credentials() -> tuple[str, str]:
    """Resolve tool-service credentials without enabling an inference provider.

    Keep existing encrypted pools usable for media jobs. Select once per job so
    its submit, poll and download use the same account and scoped endpoint.
    """
    from agent.credential_pool import get_env_prefer_dotenv, load_pool

    entry = load_pool("openrouter").select()
    endpoint = get_env_prefer_dotenv("OPENROUTER_BASE_URL")
    return ((entry.access_token or "").strip() if entry else "",
            (endpoint or (entry.base_url if entry else "") or "https://openrouter.ai/api/v1").rstrip("/"))


def check_api_key() -> bool:
    """Return True if OPENROUTER_API_KEY is present.

    Scope-aware: an installed profile secret scope is authoritative under
    multiplex; unscoped CLI probes fall back to the plain env read. Any other
    scope failure propagates -- a failed scoped read must never silently
    borrow another profile's key from ``os.environ``.
    """
    from agent.secret_scope import UnscopedSecretError, get_secret

    try:
        return bool(get_secret("OPENROUTER_API_KEY"))
    except UnscopedSecretError:
        return bool(os.getenv("OPENROUTER_API_KEY"))
