---
sidebar_position: 5
title: "Provider Maintenance"
description: "Maintain the bounded provider catalog: auth, protocols, context management, recovery, and acceptance."
---

# Provider maintenance

This fork maintains a [small default catalog](../integrations/providers.md).
Adding another vendor is not the default solution to a connection request.
A named custom endpoint already covers compatible OpenAI Chat Completions,
OpenAI Responses, and Anthropic Messages services. Compatibility must be verified
against the endpoint's actual protocol and capabilities, not inferred from its name.

For a third-party integration, prefer a standalone
[model provider plugin](./model-provider-plugin.md). Do not add a bundled plugin,
menu entry, or dependency merely because an upstream integration exists.
A proposed default integration must meet the
[product scope and qualification contract](./reliability.md#product-scope) first.

## Maintain the whole connection

A provider is more than login and one successful chat request:

- Auth and token refresh select only the intended endpoint's credentials.
- Setup, `hermes model`, `/model`, aliases, and configured fallback routes agree.
- Main and auxiliary calls use the selected protocol and supported request fields.
- Usage, context/output limits, tool calls, compaction, and resume remain consistent.
- Rate limits, interrupted streams, auth failures, and unavailable models recover
  within bounded budgets without duplicating tool side effects.
- Install, update, and repair preserve encrypted credentials and route configuration.

Do not silently choose another vendor when configuration or authentication fails.

## Where the behavior lives

Read each area's `AGENTS.md` before editing. Facades expose entry points; keep new
behavior in the appropriate topic module rather than growing a facade.

| Concern | Starting point |
| --- | --- |
| Provider profile and extension discovery | `providers/base.py`, `providers/__init__.py`, `plugins/model-providers/` |
| Credential resolution and refresh | `hermes_cli/auth.py` and its topic modules |
| Endpoint and protocol resolution | `hermes_cli/runtime_provider.py`, `hermes_cli/providers.py` |
| Model catalog, aliases, and setup | `hermes_cli/models_catalog_static.py`, `hermes_cli/models.py`, `hermes_cli/main.py` |
| Wire conversion and request dispatch | `agent/transports/`, provider adapters, `agent/turn_*.py` |
| Auxiliary calls and context limits | `agent/auxiliary_client.py`, `agent/model_metadata.py` |
| Qualification matrix | `scripts/e2e/provider_matrix.py` |

Trace production callers before changing a symbol. A catalog entry alone does not
prove that every CLI, gateway, auxiliary, or restore path uses the same route.

## Protocol boundaries

| API mode | Wire protocol | Maintained examples |
| --- | --- | --- |
| `chat_completions` | OpenAI Chat Completions | Z.AI, Vercel, LiteLLM, custom OpenAI |
| `codex_responses` | OpenAI Responses | ChatGPT/Codex, Grok, compatible custom endpoints |
| `anthropic_messages` | Anthropic Messages | Anthropic, compatible gateways/custom endpoints |

A service supporting one protocol is not automatically qualified for the others.
Do not send vendor-only request fields to a generic endpoint. Respect the actual
model's reasoning vocabulary, tool capabilities, and context/output budgets.

History is stored in the internal chat-completions representation. Transports
convert it to the selected wire format while preserving complete tool-call/result
pairs and the stable cached conversation prefix. Apply route and tool changes to
the next session unless the user explicitly opts into immediate activation.

## Required evidence

Use `scripts/run_tests.sh`, never bare pytest, and isolate all homes and credentials.

1. Reproduce the failure on current `main`; inspect the original implementation's
   intent. Add a small behavioral regression proven red on the base.
2. Exercise the real agent, imports, tools, encrypted session DB, growing history,
   compaction, and resume through the affected protocol. Include relevant fault
   recovery, not only mocked SDK responses.
3. For live qualification, test the actual connection, model, and native OS.
   A LiteLLM result does not qualify a direct-vendor account; Linux does not qualify
   macOS. Missing secrets, skips, cancellations, and retry-masked failures are not passes.
4. For installation/state changes, run the affected disposable cold lifecycle
   scenarios and prove state preservation and unlock counts.
5. Record the exact commit, environment, protocol, checks, and cleanup. Never upload
   credentials, decrypted homes, or personal subscription tokens.

Keep the catalog, setup UX, protocol matrix, and user guide consistent in the same
change. Retiring a provider removes default discovery, not saved user state:
preserve credentials and document an explicit supported migration route.

## Related guides

- [Provider runtime resolution](./provider-runtime.md)
- [Model provider extension API](./model-provider-plugin.md)
- [Context compression and caching](./context-compression-and-caching.md)
- [Reliability and qualification](./reliability.md)
