"""Route-specific compaction policy for the Codex OAuth context limit."""

from typing import Optional


def _is_codex_gpt54_or_gpt55(model: Optional[str], provider: Optional[str] = None) -> bool:
    """True for gpt-5.4/5.5/5.6, gpt-6 Sol/Terra/Luna, gpt-6 Astra (and the Daybreak Sol alias) on the Codex OAuth route only.

    Other routes expose a larger window for the same slug and keep the user's threshold.
    Prefix-matched so ``-pro`` and dated snapshots track every 272K-capped family; ``-900k``
    picker variants are excluded. Astra is substring-matched (any slug containing ``astra``
    without ``900k``). Name kept for the ``compression.codex_gpt55_autoraise`` key.
    """
    if (provider or "").strip().lower() != "openai-codex":
        return False
    bare = (model or "").strip().lower().rsplit("/", 1)[-1]
    from agent.model_metadata import is_codex_context_variant
    if is_codex_context_variant(bare):
        return False
    if "astra" in bare:
        return "900k" not in bare
    from agent.reasoning_effort import is_gpt61_model

    return bare == "gpt-daybreak-blue-latest" or is_gpt61_model(bare) or any(
        bare == fam or bare.startswith(fam + "-") or bare.startswith(fam + ".")
        for fam in ("gpt-5.4", "gpt-5.5", "gpt-5.6", "gpt-6-sol", "gpt-6.1-sol", "gpt-6-luna"))
