# Bundled model providers

The supported inference surface is deliberately bounded: ChatGPT/Codex, Anthropic,
LiteLLM, custom OpenAI/Anthropic endpoints, Z.AI, Grok and Vercel AI Gateway. Direct
OpenAI API credentials are also supported as the OpenAI-compatible API route.

Each directory contains a provider profile and manifest. Do not add another bundled
vendor by default. User-installed provider plugins remain supported through the
existing provider registry and profile-scoped discovery.

A model's author is not its transport provider: Vercel, LiteLLM and custom endpoints
can serve models from any vendor. Do not strip model-family compatibility from the
generic transports when reducing the bundled provider menu.

LiteLLM's endpoint belongs in `providers.litellm.base_url` in `config.yaml`; the
secret is referenced with `key_env`. No behavioral settings belong in `.env`.
