---
title: "LLM and Model Providers"
sidebar_label: "AI Providers"
sidebar_position: 1
---

# LLM and Model Providers

This encrypted fork has a deliberately small supported inference surface. Choose a
provider with `hermes model` or `hermes setup`; select a model with `/model` in chat.
A fresh install requires an explicit connection. There is no anonymous or silent
fallback to a different vendor.

| Connection | Provider ID | Authentication |
|---|---|---|
| ChatGPT / Codex subscription | `openai-codex` | Sign in using `hermes auth add openai-codex` |
| Anthropic / Claude | `anthropic` | API key or existing Claude Code authentication |
| OpenAI API | `openai-api` | `OPENAI_API_KEY` |
| LiteLLM gateway | `litellm` | Your configured proxy URL and gateway key |
| Custom OpenAI / Anthropic API | `custom` / `custom:<name>` | Named endpoint and credential reference |
| Z.AI / GLM | `zai` | `GLM_API_KEY`, `ZAI_API_KEY` or `Z_AI_API_KEY` |
| Grok | `xai` / `xai-oauth` | `XAI_API_KEY` or subscription OAuth |
| Vercel AI Gateway | `ai-gateway` | `AI_GATEWAY_API_KEY` |

Aliases include `chatgpt`, `claude`, `glm`, `grok` and `vercel`. `openai` means the
OpenAI API, never OpenRouter. Fireworks and other direct providers are not bundled.
Previously saved credentials are preserved, but a removed provider must be replaced
with an explicit supported route. An explicitly installed provider plugin can add an
edge capability without expanding the default product menu.

A model's author is not its transport provider. LiteLLM, Vercel and custom endpoints
can serve models from other vendors; their model families and wire compatibility
remain available.

## LiteLLM and custom endpoints

Endpoint settings belong in `config.yaml`; API keys belong in the encrypted secret
store and are referenced with `key_env`. For example:

### Named custom providers

Name each endpoint once under `providers:` and select that name when configuring
an auxiliary task or model route. `extra_body` may contain request fields your
gateway explicitly supports; it is not a universal provider-routing format.

```yaml
model:
  provider: litellm
  default: your-gateway-model
providers:
  litellm:
    base_url: https://gateway.example.com/v1
    key_env: LITELLM_API_KEY
  private-claude:
    base_url: https://claude.example.com
    api_mode: anthropic_messages
    key_env: PRIVATE_CLAUDE_API_KEY
  private-openai:
    base_url: https://openai.example.com/v1
    api_mode: chat_completions
    key_env: PRIVATE_OPENAI_API_KEY
```

Use `hermes model` for credential entry. Inside an unlocked session, set endpoint
settings with normal CLI commands through the terminal tool:

```sh
hermes config set providers.litellm.base_url https://gateway.example.com/v1
hermes config set providers.litellm.key_env LITELLM_API_KEY
hermes config set model.provider litellm
hermes config set model.default your-gateway-model
hermes config check
```

Do not write YAML over encrypted files. New settings take effect in the next
session unless an operation explicitly supports immediate activation.

Use `anthropic_messages` for a custom Anthropic Messages endpoint,
`chat_completions` for OpenAI Chat Completions, or `codex_responses` for an endpoint
that actually supports OpenAI Responses. A provider URL is not an API credential.
Local OpenAI-compatible servers are custom endpoints, not separate bundled vendors.

Before first use of an Anthropic Messages route, prepare its optional SDK with
`hermes pm install --extra anthropic`, then start a new session. This applies to
Anthropic, custom Anthropic endpoints, and gateways using `anthropic_messages`.

## Context length detection

Hermes resolves context capacity from the selected provider/model catalog and
endpoint capabilities, then applies explicit model overrides. A model name alone
does not prove a gateway exposes the same context window as the first-party API.
Inspect the selected model with `hermes model`. If your endpoint has a known limit,
set `model.context_length` through `hermes config set`, not by editing ciphertext.
Keep the configured window within the endpoint's actual capacity. See
[context compression](../user-guide/configuration.md#context-compression) for
compaction thresholds and per-model overrides.

## Tool calls appear as text instead of executing

A custom server must parse the selected model's tool-call format. If the model
prints a tool-call JSON object as ordinary text, check the server configuration:

| Custom server | Check |
|---|---|
| llama.cpp | Enable its tool-aware chat template (`--jinja`) |
| vLLM | Enable automatic tool choice and a tool-call parser matching the model |
| Ollama or another OpenAI-compatible server | Use a tool-capable model and the server's OpenAI-compatible endpoint |

These are custom endpoint integrations, not additional bundled providers.

## Grok prompt caching

Keep the selected xAI route, model, and session stable. Hermes owns the request
identity and conversation prefix; rebuilding the live system prompt or tool schema
discards cached context. Apply model/tool configuration changes to the next session
unless you deliberately choose an operation's immediate-activation option.

## Credentials, profiles and updates

`hermes auth` manages multiple credentials and rotation. Profiles remain independent:
a session's unlock and credentials do not authorize another profile. During an
update, encryption, credentials, model choices and history must survive unchanged.

A local CLI or delegated agent inside an unlocked Hermes session reuses that
session's temporary authority without another passphrase. A standalone interactive
update unlocks once, then hands authority to its own verification children. The
passphrase is never exported or saved. See [updating](../getting-started/updating.md)
and the bundled Hermes guide's encrypted self-management reference.

## Explicit fallbacks

Use `hermes fallback add`, `hermes fallback list` and `hermes fallback remove` to
manage deliberate fallback routes. Only supported or explicitly configured custom
routes may be selected. A missing credential, invalid config or unavailable primary
must not silently send a request to an unrelated provider.

## Legacy plugin configuration

Updates preserve legacy settings without enabling retired providers. For an
explicitly installed OpenRouter provider plugin, `openrouter.min_coding_score`
controls its Pareto-code router threshold (0–1; default 0.65). It has no effect on
the bundled providers or on OpenRouter media-tool credentials.
