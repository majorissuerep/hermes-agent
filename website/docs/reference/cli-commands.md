---
title: "CLI commands"
description: "Native terminal commands for the encrypted Hermes fork"
---

# CLI commands

`hermes --help` and `hermes <command> --help` are authoritative for the installed
version. Bare `hermes` or `hermes chat` launches the local Ink TUI; a query uses
the same agent core headlessly. Desktop, messaging gateways, HTTP management
servers and inbound MCP/ACP servers are not shipped.

## Conversation

```sh
hermes
hermes --continue
hermes --resume SESSION_ID
hermes chat -q "A single verifiable task"
hermes --profile NAME chat -q "A task for this profile"
```

Use `--model` / `--provider` for invocation overrides, `--toolsets` / `--skills`
for initial capability selection, and `--max-turns` to bound iterations.
`--safe-mode` disables customization for diagnosis; give an explicit model/provider
when ignoring profile configuration. `--native` selects primary-buffer TUI rendering.
See [TUI](../user-guide/tui.md) for in-session `/model`, `/reasoning`, session browsing
and approval controls. Unsupported flags fail instead of silently being dropped.

## Setup and configuration

```sh
hermes setup
hermes model
hermes auth --help
hermes config show
hermes config get agent.max_turns
hermes config set agent.max_turns 30
hermes config check
hermes tools
hermes fallback --help
```

Settings and secrets are encrypted. Never edit `config.yaml`, `.env`, auth files
or the session DB as plaintext. An unlocked local session and its terminal
children reuse profile-scoped temporary authority; standalone commands unlock
independently. Capability changes normally activate in the next session.

## Update and recovery

```sh
hermes update --check
hermes update --backup
hermes doctor
hermes pm repair
hermes secure-vault status
hermes secure-vault repair
hermes backup
hermes import --help
```

`hermes backup --quick` creates a critical-state snapshot; a full backup and a
profile export have different scope. Close local sessions before restore,
profile deletion or other exclusive maintenance. See [encrypted state](../user-guide/encrypted-state.md)
and [installation](../getting-started/installation.md). Bulk plaintext recovery
is an explicit offline export, not disabling encryption in place.

## Local extensions and work

| Command | Purpose |
| --- | --- |
| `hermes skills --help` | Browse, install, configure and maintain skills |
| `hermes plugins --help` | Explicitly install and enable plugin capabilities |
| `hermes mcp --help` | Configure outgoing stdio/HTTP tool connections |
| `hermes sessions --help` | Browse, rename, export and prune local history |
| `hermes profile --help` | Isolated local profiles, clone/export/import |
| `hermes project --help` | Named local workspaces |
| `hermes cron --help` | Local durable jobs, explicit tick/run and run history |
| `hermes kanban --help` | Local work queues and terminal workers |
| `hermes memory --help` | Memory provider settings |
| `hermes curator --help` | Skill maintenance and recovery |
| `hermes logs --help` | Local agent/error logs |
| `hermes completion --help` | Shell completion |

Cron output stays local and encrypted. There is no remote delivery target or
installed background scheduler service. Agent delegation is process-local;
terminal worker/job lifetimes and exit status remain observable.

## MCP example

```sh
hermes mcp list
hermes mcp add time --command uvx --args mcp-server-time
hermes mcp test time
```

Place `--args` last. Install only tools you trust. Saving a connection does not
prove it works: inspect the probe result, then start a new session for the
changed tool surface. Do not reload tools mid-conversation without an explicit
user decision about the prompt-cache cost.
