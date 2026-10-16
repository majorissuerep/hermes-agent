# Hermes Agent

A personal AI agent for **Linux and macOS**: terminal and desktop interfaces,
remote messaging, tools, memory, skills, scheduled work, and delegated agents.
The priority is a small, dependable product: reliable conversations, encrypted
state, and safe install/update/recovery. New integrations are not a goal in
themselves; see the [product scope](website/docs/developer-guide/reliability.md#product-scope).
This is an unofficial fork of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent),
maintained by [@majorissuerep](https://github.com/majorissuerep), with mandatory
at-rest encryption. Report fork issues [here](https://github.com/majorissuerep/hermes-agent/issues).

## Install

Review the [installer](scripts/install.sh), then run:

```sh
curl -fsSL https://raw.githubusercontent.com/majorissuerep/hermes-agent/main/scripts/install.sh | bash
hermes model
hermes
```

Use the [installation guide](website/docs/getting-started/installation.md) for
prerequisites, existing installations, and recovery. Windows hosts use WSL2;
native Windows is not supported. [Platform policy](website/docs/getting-started/platform-support.md).

## Providers

ChatGPT/Codex subscription, Anthropic, OpenAI API, LiteLLM, custom OpenAI
(Chat Completions or Responses) and Anthropic endpoints, Z.AI, Grok, and Vercel AI
Gateway. Configure with `hermes model`; see [connections and credentials](website/docs/integrations/providers.md).
Other providers are not part of the supported built-in catalog.

Conversations grow incrementally. Hermes tracks request usage and context limits,
compacts history when needed, and preserves resumable sessions. See
[context management](website/docs/developer-guide/context-compression-and-caching.md)
and the [reliability contract](website/docs/developer-guide/reliability.md) for
qualification, failure recovery, and the distinction between protocol and live tests.

## Everyday commands

```sh
hermes                    # Start a conversation
hermes setup              # Configure tools, providers, and messaging
hermes gateway setup      # Configure a messaging gateway
hermes update             # Update the owning installation
hermes doctor             # Diagnose installation and configuration
hermes secure-vault status
```

Unlock once per session. Local Hermes commands and delegated agents reuse temporary,
home-scoped session authority without another passphrase. A standalone update
unlocks once. Passphrases are never exported or saved. Use `hermes config` writers,
not plaintext edits of encrypted files. See [encrypted self-management](skills/autonomous-ai-agents/hermes-agent/references/encrypted-self-management.md).

## Guides

- [Quickstart](website/docs/getting-started/quickstart.md) · [Configuration](website/docs/user-guide/configuration.md) · [CLI](website/docs/user-guide/cli.md)
- [Messaging](website/docs/user-guide/messaging/index.md) · [Desktop](website/docs/user-guide/desktop.md)
- [Tools](website/docs/user-guide/features/tools.md) · [Skills](website/docs/user-guide/features/skills.md) · [Memory](website/docs/user-guide/features/memory.md) · [MCP](website/docs/user-guide/features/mcp.md)
- [Install/update acceptance](scripts/e2e/README.md) · [Development](CONTRIBUTING.md) · [Security](SECURITY.md)

Documentation lives in [website/docs](website/docs/index.mdx). Upstream's published
site describes upstream releases, not this fork. Product translations remain in
`locales/`; the English repository guides are the maintained source of truth.

## License

[MIT](LICENSE). Original project by Nous Research; this fork is not affiliated with
or endorsed by Nous Research.
