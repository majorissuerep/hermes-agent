"""Every bundled provider must participate in protocol qualification."""
from scripts.e2e.provider_matrix import CHAT, CONNECTIONS


def test_every_bundled_provider_and_default_transport_has_a_qualification_contract():
    from hermes_cli.models_catalog_static import CANONICAL_PROVIDERS
    from hermes_cli.providers import get_provider

    assert {entry.slug for entry in CANONICAL_PROVIDERS} <= {c.identity for c in CONNECTIONS.values()}
    for entry in CANONICAL_PROVIDERS:
        runtime = get_provider(entry.slug, allow_network=False)
        if runtime is not None:
            mode = CHAT if runtime.transport == "openai_chat" else runtime.transport
            covered = {mode for c in CONNECTIONS.values() if c.identity == entry.slug for mode in c.modes}
            assert mode in covered, f"{entry.slug}: default {mode} has no qualification contract"
