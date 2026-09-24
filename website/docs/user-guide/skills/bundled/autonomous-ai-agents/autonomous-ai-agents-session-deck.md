---
title: "Session Deck — Message, inspect or close the user's other sessions"
sidebar_label: "Session Deck"
description: "Message, inspect or close the user's other sessions"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Session Deck

Message, inspect or close the user's other sessions.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/autonomous-ai-agents/session-deck` |
| Version | `1.0.0` |
| Author | Roman S + Hermes Agent |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `Sessions`, `Deck`, `Coordination`, `Multi-Agent` |
| Related skills | [`hermes-agent`](../../bundled/autonomous-ai-agents/autonomous-ai-agents-hermes-agent.md) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Session Deck Skill

Every session the user opens stays open — across terminal exits, crashes and reboots — until it is
closed. The session deck is the list of those open sessions, and `hermes deck` lets THIS session reach
the others: hand work to one, check what another is doing, or close one the user is done with. It does
not start new agents (use `delegate_task` for sub-work you own) and it cannot act on sessions of another
machine.

## When to Use

- The user says "tell the session working on X …", "ask #4 …", "what is my other session doing", or
  "close the old research session".
- You finished work another open session is waiting on and should hand it over.
- You need a sibling session's context (its latest messages) before acting.

Do not use it to talk to yourself, and do not close sessions the user did not ask you to close.

## Prerequisites

None: `hermes deck` ships with Hermes and starts the session host on demand.

## How to Run

All verbs run through `terminal`. Your identity is attached automatically (the recipient sees
"message from session #N"), so never impersonate the user in the text.

## Quick Reference

| Goal | Command |
|---|---|
| List open sessions (all profiles) | `hermes deck ls` · `--json` for parsing |
| Read a session's latest messages | `hermes deck peek '#4' -n 8` |
| Queue a message as its next turn | `hermes deck send '#4' "text"` |
| …and wait for its reply | `hermes deck send '#4' --wait 300 "text"` |
| Long message from a file | `hermes deck send '#4' --file path/to/brief.md` |
| Stop its running turn | `hermes deck interrupt '#4'` |
| Close it for good | `hermes deck close '#4'` |

References: `#N` is session N in your profile, `work#N` in profile `work`. Always quote them — an
unquoted `#` starts a shell comment.

States: `●` running, `◐` waiting on the user, `○` idle, `◌` detached (no terminal attached, still
live), `·` dormant (not loaded; a message wakes it).

## Procedure

1. `hermes deck ls --json` and identify the target by title, cwd or recent activity. If two
   candidates fit, `peek` both, or ask the user which one they mean.
2. For a hand-off, write a self-contained message: what you did, where the artefacts are (absolute
   paths), what you need back. The recipient does not share your context.
3. `send`. Without `--wait` the message is queued and runs as the target's next turn once it is idle;
   it never interrupts a turn in progress. Use `--wait` only when you need the answer to continue,
   and keep the timeout bounded.
4. Report to the user what you sent and to whom. If you waited, relay the reply.
5. `close` only on an explicit user request naming the session; confirm the reference first.

## Pitfalls

- A session cannot message, interrupt or close itself through the deck (refused). The user ends
  the current session with `/close`.
- Waiting on a session that is itself waiting on you deadlocks until the timeout. Prefer queued
  sends plus a later `peek`.
- A session held by a process that cannot receive messages (e.g. a messaging-gateway chat) is
  refused with the owner named; tell the user rather than retrying.
- `interrupt` discards the target's in-progress turn; only do it when asked.

## Verification

`hermes deck ls` shows the target's state moving to `running` after a send, and `hermes deck peek`
shows your message and its answer once the turn completes.
