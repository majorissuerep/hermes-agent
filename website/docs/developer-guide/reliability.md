---
title: Reliability and qualification
description: "Provider, context, installation, and encrypted recovery acceptance contracts."
---

# Reliability and qualification

The supported product is Linux/macOS with the [built-in provider catalog](../integrations/providers.md).
A provider is a connection and protocol, not merely a model name. A gateway passing
Chat Completions does not qualify its Responses or Anthropic endpoint.

## Product scope

Hermes is a focused personal agent, not a catalog of every available integration.
Reliable conversations, encrypted self-management, and installation/update/recovery
take priority over expanding the feature count. The product is the local Ink TUI
and headless/self-called terminal agents on Linux and macOS. Desktop, messaging
gateways, remote management and inbound agent servers are not shipped. A private
stdio runtime is an implementation detail, not an exposed management endpoint.

- The provider catalog and platform policy define the default support boundary.
  An upstream merge, model catalog entry, or working custom URL does not expand it.
- A new default integration needs a concrete use case, an identified maintainer,
  and acceptance evidence for its actual protocols and operating systems, including
  conversation recovery and install/update compatibility. A login smoke test is not enough.
- Plugins and custom endpoints remain opt-in extension paths. Compatibility with
  an extension is not a promise to maintain every vendor or service behind it.
- Remove obsolete default choices, guides, and workflow lanes together. Preserve
  user state and explicit migration paths; do not silently delete saved credentials
  or redirect requests when narrowing support.

## Conversation contract

- Append turns and tool results; keep the cached system/tool/history prefix stable
  until an explicit context compaction. Do not reload prompts during a conversation.
- Use provider-reported usage plus newly appended content for request pressure.
  Include tools, replayed reasoning, and reserved output in context budgeting.
- Respect confirmed model/endpoint limits. A measured smaller limit overrides a
  speculative catalog value; an output-cap error must not shrink the input window.
  Recheck pressure before a recovered request.
- Compaction preserves the active request, tool-call/result pairing, and resumable
  state. A failed, empty, refused, truncated, or timed-out summary must not silently
  replace durable history. Fallbacks stay bounded and report failure honestly.
- Transient network/429/5xx errors and interrupted streams must not duplicate tool
  side effects. Execute only complete calls; honor retry timing and budgets.

OpenAI's [conversation-state](https://developers.openai.com/api/docs/guides/conversation-state)
and [compaction](https://developers.openai.com/api/docs/guides/compaction) references
specify separate output/context limits and canonical compaction output. Custom
endpoints are capability-dependent: never assume OpenAI-only compaction exists.

## Test layers

| Layer | Purpose | What it does not prove |
| --- | --- | --- |
| Behavioral regressions | Reproduce a specific failure, red on the base | Vendor availability |
| Protocol/fault E2E | Real Hermes, session DB and tools against strict loopback HTTP APIs: growing history, compaction, resume, overflow, rate limits, broken streams | Live account entitlement or gateway configuration |
| Live connection acceptance | Real endpoint, credentials, streaming, tools, growing conversation, compaction, resume | Other vendors behind the gateway or another API dialect |
| Cold lifecycle acceptance | Public installer/updater, PM, encrypted state, restore, delegation and prompt counts in disposable environments | Every arbitrary host modification |

Tests never use a real user's home. Missing credentials, skipped scenarios,
retry-masked failures, cancellations, and partial matrices are not qualification.
Record the exact SHA, OS/image, protocol, model, checks, and cleanup result.
Never upload credentials, decrypted state, or test homes.

## Installation and encrypted state

Acceptance covers clean install, older-fork update, damaged source, a previously
used plaintext home, runtime repair, and encrypted recovery. Preserve configuration,
credentials (including retired providers), sessions, memory, skills, cron,
attachments, and unrelated local work. A standalone update unlocks **once**;
local/delegated self-management requires **no additional unlock**. An independent
session still unlocks; closing the owner revokes its authority.

Use `hermes update --backup` for rollback coverage, `hermes doctor` for diagnosis,
and `hermes pm repair` for dependencies. Rerun the source installer if installed
source cannot start. Never repair by deleting the state home. `hermes secure-vault
repair` handles supported clobber shapes; retain and report unrecoverable data.

Bulk decryption is an explicit **offline export**, not in-place disabling of
mandatory encryption. Keep the encrypted original intact and verify the export
before using it for migration. See [encrypted state](../user-guide/encrypted-state.md).

## Reliability target

99.99% successful eligible operations is an engineering target, not a claim derived
from a small test run or a guarantee of an external API's uptime. Count failures,
retries, recovery, and coverage separately. A provider/OS is live-qualified only
with an exact-head receipt from that connection/OS. Gemini stays outside the
default catalog pending full qualification; Grok and Vercel remain supported.
