"""Regression for #110402: a ``providers.<name>`` block with no endpoint (only tuning keys like
``stale_timeout_seconds``/``request_timeout_seconds``) must not shadow a BUILT-IN provider of the
same name. ``providers.anthropic: {stale_timeout_seconds: 600}`` is the documented way to tune a
built-in's per-call timeout (agent/turn_recovery.py, agent/thinking_timeout_guidance.py); it is not
a custom-endpoint definition and must fall through to the real Anthropic overlay (aws_sdk auth,
anthropic_converse transport) instead of resolving to an empty ``openai_chat``/``api_key`` stub that
routes Anthropic through the generic custom-endpoint ``/models`` probe.
"""

from hermes_cli.providers import resolve_provider_full, resolve_user_provider


def test_timeout_only_entry_does_not_resolve_as_a_custom_provider():
    """A providers.<name> block with only tuning keys is not a provider definition."""
    user_providers = {"anthropic": {"stale_timeout_seconds": 600, "request_timeout_seconds": 1800}}
    assert resolve_user_provider("anthropic", user_providers) is None


def test_timeout_only_anthropic_entry_falls_through_to_the_builtin_overlay():
    """resolve_provider_full must still resolve Anthropic's real transport/auth_type, not a stub."""
    user_providers = {"anthropic": {"stale_timeout_seconds": 600, "request_timeout_seconds": 1800}}
    pdef = resolve_provider_full("anthropic", user_providers, None)
    assert pdef is not None
    assert pdef.transport == "anthropic_messages"
    assert pdef.auth_type == resolve_provider_full("anthropic", {}).auth_type


def test_entry_with_an_endpoint_still_resolves_as_a_custom_provider():
    """A real endpoint-bearing providers.<name> block keeps resolving (no over-correction)."""
    user_providers = {"local-ollama": {"api": "http://localhost:11434/v1", "name": "Local Ollama"}}
    pdef = resolve_user_provider("local-ollama", user_providers)
    assert pdef is not None
    assert pdef.base_url == "http://localhost:11434/v1"
