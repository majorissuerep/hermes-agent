#!/usr/bin/env -S bash -c 'exec "$BASH" "$(dirname "$0")/run-in-hermes-env" python3 "$0" "$@"'
"""Build the Hermes Model Catalog — a centralized JSON manifest of curated models.

This script reads the in-repo hardcoded curated lists (``OPENROUTER_MODELS``,
``_PROVIDER_MODELS["nous"]``) and writes them to a JSON manifest that the
Hermes CLI fetches at runtime. Publishing the catalog through the docs site
lets maintainers update model lists without shipping a Hermes release.

The runtime fetcher falls back to the same in-repo hardcoded lists if the
manifest is unreachable, so this script is a convenience for keeping the
manifest in sync — not a source of truth.

Usage::

    python scripts/build_model_catalog.py

Output: ``website/static/api/model-catalog.json``

Upstream reference URL (this fork does not publish a docs site):
``https://hermes-agent.nousresearch.com/docs/api/model-catalog.json``
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, UTC

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

# Ensure HERMES_HOME is set for imports that touch it at module level.
os.environ.setdefault("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes"))

from hermes_cli.models import (
    _PROVIDER_MODELS,
)

OUTPUT_PATH = os.path.join(REPO_ROOT, "website", "static", "api", "model-catalog.json")
CATALOG_VERSION = 1


def build_catalog() -> dict:
    from hermes_cli.models_catalog_static import CANONICAL_PROVIDERS
    return {
        "version": CATALOG_VERSION,
        "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metadata": {"source": "hermes-agent repo"},
        "providers": {
            provider.slug: {
                "metadata": {"display_name": provider.label},
                "models": [{"id": model} for model in _PROVIDER_MODELS.get(provider.slug, [])],
            }
            for provider in CANONICAL_PROVIDERS
        },
    }


def main() -> int:
    catalog = build_catalog()
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(catalog, fh, indent=2)
        fh.write("\n")

    print(f"Wrote {OUTPUT_PATH}")
    for provider, block in catalog["providers"].items():
        print(f"  {provider}: {len(block['models'])} models")
    return 0


if __name__ == "__main__":
    sys.exit(main())
