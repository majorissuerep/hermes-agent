"""Every bundled provider must participate in protocol qualification."""
from scripts.e2e.provider_matrix import CONNECTIONS


def test_every_bundled_provider_has_a_qualification_contract():
    from hermes_cli.models_catalog_static import CANONICAL_PROVIDERS

    assert {entry.slug for entry in CANONICAL_PROVIDERS} <= {c.identity for c in CONNECTIONS.values()}
