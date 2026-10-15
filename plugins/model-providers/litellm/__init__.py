"""LiteLLM uses an explicitly configured OpenAI-compatible gateway."""

from providers import register_provider
from providers.base import ProviderProfile

register_provider(ProviderProfile(
    name="litellm", display_name="LiteLLM", aliases=("litellm-proxy",),
    description="Your LiteLLM gateway; configure providers.litellm.base_url in config.yaml",
    env_vars=("LITELLM_API_KEY", "ML_GATEWAY_API_KEY"),
))
