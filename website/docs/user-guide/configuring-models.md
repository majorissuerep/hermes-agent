---
sidebar_position: 3
---

# Configuring Models

Hermes is a local Ink TUI and finite CLI. Use `hermes model` to configure a
provider, then `/model` inside the TUI to change the active conversation.
Credentials and settings belong to the selected encrypted profile.

## Supported routes

| Service | Provider | Authentication |
|---|---|---|
| ChatGPT / Codex | `openai-codex` | ChatGPT sign-in |
| OpenAI API | `openai-api` | OpenAI API key |
| Anthropic | `anthropic` | Anthropic credential |
| LiteLLM | `litellm` | Gateway URL and dedicated gateway key |
| Z.AI / GLM | `zai` | Z.AI API key |
| Grok | `xai` or `xai-oauth` | API key or supported subscription sign-in |
| Vercel AI Gateway | `ai-gateway` | Vercel gateway key |
| Custom endpoint | `custom` or a named `providers` entry | Endpoint-specific credential |

A model served by LiteLLM or a custom endpoint does not add another built-in
provider. Other provider integrations are not enabled by this fork.

## Switching in an active conversation

Open `/model`, choose the provider and model, then choose reasoning and scope.
You can paste the complete model ID into the filter.

- **Session:** change this conversation without changing the profile default.
- **Once:** use the selection for the next turn, then restore the session route.
- **Global:** also save the selection for future conversations in this profile.

The same conversation and history continue after a switch. Past messages,
tool definitions and the established system prompt stay frozen. Model/provider
labels in that prompt describe startup, not live routing authority; the TUI
status and appended route notices describe the current route. Compression is
the only operation that replaces established context.

Different models or accounts may have separate provider-side caches even when
Hermes preserves the logical prefix. Switching to a smaller context window
can require compression before the next answer. A refused or failed selection
must leave the existing route usable.

## Custom APIs

Custom endpoints can use OpenAI Chat Completions, OpenAI Responses, or Anthropic
Messages. Select the correct transport rather than relying on model-name guesses.
For example, a named OpenAI-compatible endpoint can declare:

```yaml
model:
  provider: company
  default: company-model
providers:
  company:
    base_url: https://llm.example.com/v1
    api_key: ${COMPANY_LLM_API_KEY}
    transport: chat_completions
```

Use `codex_responses` for an OpenAI Responses endpoint, or
`anthropic_messages` for an Anthropic-compatible Messages endpoint. Keep the
credential in the profile's encrypted environment; the config contains its
reference, not a copied secret. Configure encrypted files through `hermes setup`
or the managed config commands—do not edit ciphertext directly.

Provider entries also support `extra_headers`, `discover_models`, an explicit
`models` list, and an opt-in `session_affinity_header`. Authentication headers
must stay in the owning profile's secret scope.

## Context, timeouts and recovery

Hermes resolves the active model's context limit and compresses before the next
request when needed. ChatGPT discovery is account-specific; a local context
alias cannot exceed the account's advertised limit. Failed discovery is not a
successful catalog refresh.

For a proxy with bounded latency, configure its existing provider budgets:

```yaml
providers:
  litellm:
    request_timeout_seconds: 60
    stale_timeout_seconds: 90
```

A private-network LiteLLM proxy is not treated as slow local inference. An
entirely silent LiteLLM stream can recover once without streaming, before any
text, reasoning or partial tool action has been delivered. Successful recovery
keeps that conversation non-streaming. Delivered tool effects must not be
replayed; cancellation and route changes fence recovery.

Fallback providers are explicit. Hermes must not silently bill another account
merely because a credential happens to be present. See
[Fallback Providers](./features/fallback-providers.md) and
[Credential Pools](./features/credential-pools.md).

## Auxiliary models

Compression, vision, titles and other auxiliary tasks normally use the selected
main route. Override a task when it needs different capabilities or a smaller
model:

```yaml
auxiliary:
  compression:
    provider: litellm
    model: your-summary-model
```

`provider: auto` with an empty model restores main-route selection. Task-specific
`fallback_chain` entries take precedence over the configured main fallback chain;
there is no fallback to an unrelated logged-in provider. Start a new conversation
for changes that affect prompt construction.

Terminal subagents inherit reasoning unless `delegation.reasoning_effort` overrides
it. Noninteractive use of vendor data-training tiers remains refused unless
`security.allow_data_training_tiers_noninteractive` explicitly acknowledges it.
The legacy `nous.anthropic_wire` setting does not enable a Nous model provider.

## Troubleshooting

- **Missing provider:** run `hermes model` or `hermes setup` in the correct profile.
- **Wrong model:** inspect the TUI status and selection scope; global settings do
  not overwrite another already-running conversation.
- **Authentication or quota refusal:** check the selected account and endpoint;
  changing the model name does not repair an invalid key.
- **Growing context:** use `/usage` and `/compress`; do not delete past messages
  to force a request through.

See [Configuration](./configuration.md), [Profiles](./profiles.md) and the
[Slash Command Reference](../reference/slash-commands.md).
