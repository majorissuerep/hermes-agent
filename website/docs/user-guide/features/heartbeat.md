---
sidebar_position: 17
title: "Session Heartbeats"
description: "A recurring prompt that re-enters your current session whenever it's idle — /heartbeat every 10m Check the deployment."
---

# Session Heartbeats (`/heartbeat`)

`/heartbeat` gives the **current session** one recurring instruction. Whenever the session is idle and the interval has elapsed, the prompt fires as a normal user turn — same conversation, same context, same prompt cache.

```
/heartbeat every 10m Check the deployment and report meaningful changes
```

Inspired by Prime-Agent's `/heartbeat`. The Hermes adaptation keeps the strict message-flow invariants: the heartbeat is injected only between turns (never mid-run), as a plain user-role message.

## Heartbeat vs cron: which one do I want?

They look similar but serve different jobs:

| | `/heartbeat` | [`hermes cron`](./cron) |
|---|---|---|
| Runs in | **This conversation** — full context, memory of the discussion | A fresh isolated session per tick |
| Survives process restart | State survives (SessionDB); firing requires the owning terminal session | Yes — fully durable scheduler |
| How many | One per session | Unlimited jobs |
| Best for | "Keep an eye on X *in this thread* while we work" | Standing jobs, reports, watchdogs, deliveries |

Rule of thumb: if the recurring prompt needs the conversation's context, use `/heartbeat`. If it's a self-contained job, use cron.

## Commands

| Command | What it does |
|---|---|
| `/heartbeat every <interval> <prompt>` | Set (or replace) the session's heartbeat. Intervals: `90s`, `10m`, `2h`, `1d` (minimum 60s). |
| `/heartbeat` or `/heartbeat status` | Show the heartbeat, its interval, and time to next fire. |
| `/heartbeat pause` | Stop firing without clearing. |
| `/heartbeat resume` | Resume (re-anchors the timer — no instant stale fire). |
| `/heartbeat clear` | Remove the heartbeat. |

`/hb` is an alias in the terminal UI.

Historical messaging-session heartbeats remain stored but do not run locally automatically.
Review the instruction, then explicitly `/heartbeat resume` (or replace it) to claim local execution
in that conversation. This preserves its transcript without reviving a removed delivery route.

## Behavior details

- **Idle-only.** A heartbeat never interrupts a running turn. If the agent is busy when the tick comes due, it fires at the next idle poll.
- **Missed ticks coalesce.** If the session was busy (or the process wasn't running) through several intervals, you get **one** heartbeat turn, not a backlog. The timer re-anchors on every fire.
- **User messages win.** A queued user message always takes priority; the heartbeat waits for the input queue to drain.
- **Local ownership.** Explicit set/resume claims the current terminal destination. Unsupported historical destinations stay passive and do not consume a tick.
- **Cache-safe.** The injected prompt is an ordinary user message. No system-prompt mutation, no toolset change.
- **Persistence.** State lives in `SessionDB.state_meta` keyed by `heartbeat:<session_id>` and follows context-compression session rotations. Firing requires the owning terminal process to be running.
- **Execution accounting.** A due tick is recorded before dispatch. A dispatch that never starts is refunded unless the schedule changed. A counted fire is not proof of a successful model response; abrupt process death can prevent refund.
- **Don't-invent-work guard.** The injected prompt tells the agent to reply briefly and stop when nothing meaningful changed, so an idle heartbeat doesn't generate busywork.

## Example

```
You: /heartbeat every 15m Check whether the CI run for PR #1234 finished; summarize the result when it does

  ♥ Heartbeat set (every 15m): Check whether the CI run for PR #1234 finished; ...

[15 minutes of you working on other things in the same session]

Hermes: [Heartbeat — recurring instruction, fires every 15m]
  💻 gh pr checks 1234   (1.2s)
  CI is still running (14/37 checks complete). Nothing to report yet.
```

When the answer stops changing, `/heartbeat clear` it — or let it keep watch.
