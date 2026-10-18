---
sidebar_position: 2
title: TUI
description: "The local terminal interface for encrypted Hermes."
---

# Terminal UI

Bare interactive `hermes` starts the Ink TUI on Linux and macOS. It owns a
private Python subprocess over stdio; there is no gateway, remote attach
endpoint, dashboard, or desktop application.

## Launch and resume

```sh
hermes
hermes --tui
hermes --continue
hermes --resume "session title or id"
hermes chat -q "Complete a bounded task"
```

The last command is a direct local headless invocation, not a UI or gateway
client. Use `hermes --help` for the installed revision's flags. The old
classic REPL and `--cli` selection are retired.

## Models and conversation continuity

Use `/model` to open the picker or specify a model directly. The selected
provider/model must serve the next request while preserving the conversation.
A one-turn selection returns to the previous route afterward; it does not
rewrite profile defaults. Provider errors must remain visible without claiming
a switch succeeded.

Context usage includes the growing transcript and reserved output. Hermes
compacts through its context engine when needed; a failed summary must not
silently discard durable history. Model changes must not rewrite past turns,
the system prompt, or the tool schema just to refresh the UI.

## Encrypted local state

Unlock once at startup. The runtime keeps unlocked key material in memory,
not the passphrase. Local Hermes commands, slash configuration workers, and
terminal sub-agents reuse home/session-scoped authority without another prompt.
Independent sessions unlock normally.

Submitted history is durable in the encrypted session database. Unsent input
and pending local drafts are held only in terminal memory; they are not written
as plaintext recovery journals. Closing the terminal can discard unsent drafts.

Use `hermes config` for managed state changes. Source files in the checkout
remain ordinary code; config, credentials, history, memory, and job state use
encrypted writers. See [encrypted state](encrypted-state.md).

## Sub-agents and tools

Use `delegate_task` for isolated local subtasks or `hermes chat -q ...`
for independent terminal work. Completion, steering, cancellation, and process
ownership stay local. Closing a child does not revoke the parent session's
authority. Outgoing model, browser, and MCP connections remain available;
they do not create an inbound management service.

## Requirements and troubleshooting

The PM-managed launcher provisions Python, Node, dependencies, and the Ink
bundle. Use `hermes doctor` for diagnosis, `hermes pm repair` for runtime
repair, and `hermes update` for transactional source updates. Do not delete
the encrypted home to make startup succeed.

In-session configuration that affects prompts, memories, or tool schemas
normally applies to the next session. Use an explicitly supported reload only
when you intend that cache boundary.
