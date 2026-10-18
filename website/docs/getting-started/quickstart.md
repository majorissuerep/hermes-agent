---
sidebar_position: 1
title: "Quickstart"
description: "Start an encrypted local Hermes conversation, switch models and resume safely"
---

# Quickstart

This unofficial fork provides a local TUI and headless terminal agents for Linux
and macOS. It does not include Desktop or a messaging/remote-management gateway.

## 1. Install and unlock

```sh
curl -fsSL https://raw.githubusercontent.com/majorissuerep/hermes-agent/main/scripts/install.sh | bash
```

Open a new shell. Check the vault and complete setup:

```sh
hermes secure-vault status
hermes setup
```

If no vault exists, run `hermes secure-vault migrate` first. Choose the passphrase
in its terminal prompt and retain the recovery material. See [installation](./installation.md)
for migration, backups and optional dependencies.

## 2. Choose a connection

```sh
hermes model
```

Choose ChatGPT/Codex, Anthropic, OpenAI API, LiteLLM, custom OpenAI/Anthropic,
Z.AI, Grok or Vercel. Models from other vendors may be available through your
chosen gateway; that is not a separately supported direct provider.
For custom connections, select the actual protocol (Chat Completions, Responses,
or Anthropic Messages), endpoint, model, credential and context limit.
See [providers](../integrations/providers.md).

Secrets and configuration are encrypted at rest. Use the model picker and
`hermes config get` / `hermes config set` rather than editing ciphertext.

## 3. Start the TUI

```sh
hermes
```

Try a verifiable task:

```text
Read this repository's README and identify its entry point. Do not modify files.
```

Use `/model` for the model picker or `/model <model>` for a session switch.
Use `/reasoning` for reasoning effort. A one-turn model override should restore
the session model afterward. Model changes retain conversation history; tool and
system-prompt changes normally wait for a new session to preserve prompt caching.
See the [TUI guide](../user-guide/tui.md) for keyboard controls and commands.

Exit, then verify resume:

```sh
hermes --continue
```

An independent session unlocks once. Local `terminal` calls and `delegate_task`
children inside an unlocked session reuse its temporary authority: do not copy a
passphrase or raw vault key to them. That authority is not transferable to a
remote machine or another profile.

## 4. Use a headless terminal agent

```sh
hermes chat -q "Read README.md and summarize the installation requirements."
```

This runs the same native agent core without a UI or server. Explicit
`--model`, `--provider`, `--toolsets` and iteration limits are available through
`hermes chat --help`. Missing unlock authority is an error, not permission to
create plaintext state or silently use another provider.

## Maintain the installation

```sh
hermes update --check
hermes update --backup
hermes doctor
hermes backup
```

Restore only after closing other local sessions. Use `hermes secure-vault repair`
for supported clobber recovery and `hermes pm repair` for managed dependencies.
Never fix startup by deleting your home or replacing encrypted files with plaintext.

Enable optional tools with `hermes tools`; use a new session afterward. Outgoing
MCP connections and plugins add local capabilities without adding a management
server. Cron is explicitly driven by `hermes cron tick` or `hermes cron run`;
no gateway or scheduler service is installed.

Troubleshooting starts with `hermes doctor`, `hermes logs` and a small real tool
request. For quality criteria and what each test layer proves, see the
[reliability contract](../developer-guide/reliability.md).
