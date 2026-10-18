---
name: hermes-agent
description: "Operate encrypted, terminal-only Hermes Agent."
version: 3.5.0
author: Teknium + Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [hermes, configuration, multi-agent, terminal, encryption, tui]
    homepage: https://github.com/majorissuerep/hermes-agent
---

# Hermes Agent Skill

Operate this encrypted Linux/macOS fork through its local terminal interface.
It provides an Ink TUI, direct headless calls, tools, memory, skills, profiles,
and local sub-agents. Desktop, messaging gateways, dashboards, IDE servers, and
remote-management listeners are not available.

## When to Use

Use for Hermes configuration, model selection, updates, repair, session recovery,
or local sub-agent orchestration. Before changing encrypted state, read
[encrypted self-management](references/encrypted-self-management.md).

## Prerequisites

Use the native `terminal`, `read_file`, `patch`, and `delegate_task` tools.
A human unlocks the active home once. Local authorized children inherit temporary
session authority; never ask for the passphrase in chat or manually forward keys.
Each profile needs its own authority.

The maintained source is this fork, not upstream's website. Verify command syntax
with `hermes --help` and `hermes <command> --help` through `terminal`.
Read repository guides for the installed revision before assuming a feature exists.

## How to Run

```sh
hermes                         # Interactive Ink TUI
hermes chat -q "Complete a bounded task"
hermes model                   # Configure a supported connection
hermes config check
hermes doctor
hermes update
hermes secure-vault status
```

## Quick Reference

| Need | Reference |
| --- | --- |
| Encrypted config, repair, update, export, child authority | [Self-management](references/encrypted-self-management.md) |
| Provider/model connection | [Providers and models](references/providers-and-models.md) |
| CLI and session options | [CLI](references/cli-reference.md) |
| Configuration writers and scopes | [Configuration](references/configuration.md) |
| Project context and cached prompts | [Context files](references/project-context-files.md) |
| Outgoing MCP clients | [Native MCP](references/native-mcp.md) |
| Delegation limits and diagnosis | [Delegation](references/delegate-task-concurrency-diagnosis.md) |

## Procedure

1. Inspect the active installation, home, provider, and requested operation.
   Do not edit a different installed checkout merely because the development
   workspace contains a newer version.
2. Use existing local CLI writers for config, secrets, memory, and skills.
   Runtime files are encrypted state, not ordinary text files.
3. For quick subtasks use `delegate_task`; children have isolated conversation
   state without another unlock. For independent bounded processes use
   `terminal` to call `hermes chat -q ...`. Use isolated worktrees for
   concurrent source edits and respect completion/error outcomes.
4. For in-session switching use `/model` in the TUI. Confirm the actual next
   request/provider behavior, retained history, and errors; a success banner
   alone is not proof. A one-shot pick must not overwrite persistent defaults.
5. Apply prompt/tool/memory-affecting changes next session unless the command
   explicitly supports a deliberate immediate reload. Do not rewrite a warm
   prompt or past transcript as part of a model/config change.
6. Update transactionally with `hermes update`. Keep snapshots, diagnostics,
   and retryable completion debt; never repair by deleting a user's home.
7. Local scheduled jobs run only through explicit local cron commands. There
   is no background gateway scheduler or remote delivery.

## Pitfalls

- No `hermes desktop`, `gateway`, `dashboard`, `serve`, ACP,
  inbound proxy, or MCP `--via-host` operation exists.
- Never print credentials, passphrases, raw keys, or session grants. Remote
  terminal backends do not receive local vault authority.
- Never interpret unreadable config/history as empty state. Preserve the
  encrypted original and report repair failures.
- ChatGPT/Codex, Anthropic, OpenAI, LiteLLM, custom OpenAI (Chat Completions
  or Responses), custom Anthropic, Z.AI, Grok, and Vercel are the bounded
  built-in connections. A model catalog entry is not a new supported provider.
- Never claim a vendor/OS is qualified based on mocks, skipped tests, another
  endpoint's success, or an older commit's receipt.

## Verification

Check the public command outcome, encrypted managed reads, preserved session
history, and absence of extra unlock prompts in local children. After update,
check version, `hermes config check`, vault status, and a real conversation.
Qualification uses disposable homes and real TUI keyboard/model requests, never
the user's real installation or saved credentials.
