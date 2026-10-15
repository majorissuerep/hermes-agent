"""Provider qualification without mistaking local model tags for routing."""
from __future__ import annotations

import re

_OLLAMA_TAG_PATTERN = re.compile(r"^(\d+\.?\d*b|latest|stable|q\d|fp?\d|instruct|chat|coder|vision|text)", re.IGNORECASE)


def _strip_provider_prefix(model: str) -> str:
    """Strip a registry-known provider prefix: ``"local:m"`` -> ``"m"``. Ollama ``model:tag``
    ids are preserved even when the model half is a provider name (``qwen:0.5b``)."""
    if ":" not in model or model.startswith("http"):
        return model
    prefix, suffix = model.split(":", 1)
    try:
        from providers import get_provider_profile
        canonical = prefix.strip().lower()
        # Direct OpenAI API is auth-registry-owned, not a bundled profile.
        is_provider = canonical in {"openai", "openai-api"} or get_provider_profile(canonical) is not None
    except Exception:
        is_provider = False
    if is_provider and not _OLLAMA_TAG_PATTERN.match(suffix.strip()):
        return suffix
    return model

