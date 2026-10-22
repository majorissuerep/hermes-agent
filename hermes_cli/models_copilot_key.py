"""GitHub Copilot catalog credentials, split out of ``hermes_cli.models``.

Three-token resolution for the Copilot model catalog: env vars / ``gh auth token``,
the ``auth.json`` credential pool, and the GitHub Copilot CLI's own plaintext
``~/.copilot/config.json`` store. Helpers defined here are looked up on
``hermes_cli.models`` at call time where tests patch them (same shape as
``hermes_cli.models_local``).
"""

from __future__ import annotations

import json
import os


def first_exchangeable_copilot_token(raw_tokens) -> str:
    """Exchange stored GitHub tokens in order; the first that validates AND exchanges wins (every
    entry is tried so a later valid token survives an earlier malformed one)."""
    from hermes_cli.copilot_auth import exchange_copilot_token, validate_copilot_token

    for raw in raw_tokens:
        raw = str(raw or "").strip()
        if not raw or not validate_copilot_token(raw)[0]:
            continue
        try:
            api_token = exchange_copilot_token(raw)[0]  # (api_token, expires_at, base_url)
        except Exception:  # health: allow BLE001 -- best-effort probe across arbitrary stored tokens; a failing/malformed entry means "try the next one", never abort the catalog lookup
            continue
        if api_token:
            return api_token
    return ""


def copilot_cli_config_tokens() -> list[str]:
    """``copilotTokens`` from the GitHub Copilot CLI's own plaintext store (JSONC — strip
    ``//``-comment lines), written by ``copilot login`` on hosts without an OS keychain."""
    cli_config = os.path.expanduser("~/.copilot/config.json")
    if not os.path.isfile(cli_config):
        return []
    with open(cli_config, "r", encoding="utf-8-sig", errors="ignore") as fh:
        raw_text = "\n".join(
            line for line in fh.read().splitlines() if not line.lstrip().startswith("//"))
    data = json.loads(raw_text) if raw_text.strip() else {}
    tokens = data.get("copilotTokens")
    return list(tokens.values()) if isinstance(tokens, dict) else []


def resolve_copilot_catalog_api_key(*, api_key_credentials, read_credential_pool) -> str:
    """Best-effort GitHub token for the Copilot catalog: env vars / ``gh auth token`` via
    ``resolve_api_key_provider_credentials``, then ``auth.json`` ``credential_pool.copilot[]``, then
    ``~/.copilot/config.json`` ``copilotTokens`` (the ACP CLI's own store). Without the latter two,
    keyless users see the picker fall back to the stale curated list on a silent 401.

    The two callables are injected so this module owns no import of ``hermes_cli.models``
    (cycle-free); ``hermes_cli.models._resolve_copilot_catalog_api_key`` passes its own
    ``_api_key_credentials`` and an ``auth.json`` pool reader."""
    def _pool_token() -> str:
        return first_exchangeable_copilot_token(
            entry.get("access_token") for entry in read_credential_pool() if isinstance(entry, dict))

    sources = (
        lambda: api_key_credentials("copilot")[0],
        _pool_token,
        lambda: first_exchangeable_copilot_token(copilot_cli_config_tokens()),
    )
    for source in sources:
        try:
            token = source()
        except Exception:  # health: allow BLE001 -- ordered best-effort sources over arbitrary env/auth stores; one failing source means "fall to the next", the catalog must still resolve
            continue
        if token:
            return token
    return ""
