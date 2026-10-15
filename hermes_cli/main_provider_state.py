"""Envelope-aware on-disk readiness probes for the CLI and profile setup surfaces."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _dotenv_has_provider_key(env_file: Path, provider_env_vars: set) -> bool:
    """Whether the profile's .env assigns a non-empty inference credential."""
    if not env_file.exists():
        return False
    try:
        from hermes_security.io import read_text
        text = read_text(env_file, purpose="env", encoding="utf-8-sig") or ""
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            line = line.removeprefix("export ")
            key, _, val = line.partition("=")
            if key.strip() in provider_env_vars and val.strip().strip("'\""):
                return True
    except Exception:
        logger.debug("Could not inspect scoped provider credentials", exc_info=True)
    return False


def _auth_store_logged_in(auth_file: Path, registry, strict_profile_scope: bool) -> bool:
    """True if auth.json's active provider is logged in (api_key providers ignored under strict scope)."""
    from hermes_cli.auth import get_auth_status, _load_auth_store

    if not auth_file.exists():
        return False
    try:
        auth = _load_auth_store(auth_file)
        active = auth.get("active_provider")
        active_config = registry.get(str(active or "").strip().lower())
        if active and active_config is not None and not (
            strict_profile_scope and active_config.auth_type == "api_key"
        ):
            return bool(get_auth_status(active).get("logged_in"))
    except Exception:
        logger.debug("Could not inspect scoped provider credentials", exc_info=True)
    return False
