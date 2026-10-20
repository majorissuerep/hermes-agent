# Configuration and toolsets

Use native CLI readers/writers through `terminal`:

```sh
hermes config get agent.max_turns
hermes config set agent.max_turns 30
hermes config check
hermes model
hermes tools
```

For machine-readable inspection and atomic config batches, use
`hermes manage schema` followed by `hermes manage apply` with one JSON stdin
request. Paths are arrays (including keys containing dots); writes require the
revision returned by `config.get`. Inspection is redacted and never expands
secret references. Secret/passphrase changes use a distinct inherited one-shot
FD, never literal values in command arguments or JSON. See
`encrypted-self-management.md`; changes default to the next session.

Settings (`config.yaml`), secrets (`.env` / auth stores) and conversation state are
encrypted at rest. Do not read or replace ciphertext through plaintext file tools.
Local calls and delegated terminal agents reuse the unlocked session's temporary,
profile-scoped authority. See `encrypted-self-management.md`.

## Main sections

| Section | Purpose |
| --- | --- |
| `model` / `providers` | Explicit model, endpoint, protocol, credential reference and limits |
| `agent` | Iteration limits, reasoning and run budget |
| `compression` | Pressure threshold, target size and bounded summary fallback |
| `terminal` | Local or explicitly selected execution backend and work directory |
| `delegation` | Child concurrency, depth, model and iteration bounds |
| `approvals` | Local consent policy |
| `memory` | Persistent memory and write policy |
| `display` | TUI rendering, reasoning, timestamps and notifications |
| `mcp_servers` | Outgoing tool connections |
| `plugins` | Explicit local extension activation |
| `cron` | Local manual tick/run policy |

`hermes` always starts the TUI when interactive. Old `display.interface` settings do
not select a removed classic frontend. No Desktop, gateway, messaging adapter or
remote-management setting is active in this fork.

## Tool selection and prompt caching

Use `hermes tools` or `hermes tools enable/disable NAME`; inspect `hermes tools --help`
for the installed version. Built-in toolsets include terminal/file, web/browser,
memory/skills, delegation, cron and optional outgoing integrations. Local computer
control is an optional tool capability, not a Hermes Desktop application.

A toolset change activates next session. Do not rebuild the system prompt, reload
memory or swap tool schemas mid-conversation. Context compression is the explicit
exception. Provider support is narrower than arbitrary vendor names appearing in
optional voice/image tools; see `providers-and-models.md` for conversation routes.
