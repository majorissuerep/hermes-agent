"""Test that fallback-chain identity dedup normalizes provider aliases.

A provider can appear under two ids that are the same backend: the canonical
models.dev id (`zai`) and a rename alias (`glm`, the legacy
pre-rename name). `_entry_identity` must collapse these to one identity so the
fallback chain does not keep two routes to the same backend (which shifts the
chain by one slot and lets an alias slip past the same-backend skip).
"""

from hermes_cli.fallback_config import get_fallback_chain


def _entry(provider, model, base_url=""):
    entry = {"provider": provider, "model": model}
    if base_url:
        entry["base_url"] = base_url
    return entry


def test_fallback_chain_dedups_canonical_id_and_alias():
    config = {
        "fallback_providers": [
            _entry("zai", "glm-5.3"),
            # ``glm`` is the legacy pre-rename name of ``zai``
            # (same backend). It must not add a second slot in the chain.
            _entry("glm", "glm-5.3"),
            _entry("anthropic", "claude-sonnet-4"),
        ]
    }

    chain = get_fallback_chain(config)

    # The two zai ids describe the same backend; expect a single slot.
    providers = [e["provider"] for e in chain]
    assert providers == ["zai", "anthropic"], providers
