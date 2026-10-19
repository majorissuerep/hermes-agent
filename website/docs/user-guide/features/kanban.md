---
sidebar_position: 12
title: "Kanban (Local Multi-Agent Board)"
description: "Durable local work queues, profile workers, review and recovery"
---

# Kanban — local multi-agent work

Kanban coordinates durable work across named Hermes profiles. You use the TUI
or CLI; assigned workers use the dedicated `kanban_*` tools. Boards, runs,
comments, review handoffs and attachments survive a terminal restart.

**Execution is explicit.** Creating a task does not start a service. Run
`hermes kanban dispatch` for one local pass, then run another pass when dependent
work becomes ready. There is no Desktop, dashboard, messaging gateway or daemon.
For a walkthrough, see the [local tutorial](./kanban-tutorial).

## Kanban vs. `delegate_task`

| Use | Prefer |
| --- | --- |
| Parallel help during the current conversation, with results returned to its parent | `delegate_task` |
| Durable queued work, separate profile/model settings, dependencies and review | Kanban |

A delegated child must not complete its parent's Kanban task. Worker ownership
uses the actual dispatched process/run, not merely an inherited task ID.

## Quick start

Configure the worker profile and its provider first. Then, from an unlocked
Hermes session's terminal tool or an independent local shell:

```sh
hermes kanban init
hermes kanban create "Review the parser" --assignee reviewer \
  --body "Find reproducible defects and include verification evidence."
hermes kanban list
hermes kanban dispatch --dry-run
hermes kanban dispatch --max 1
hermes kanban watch
```

Use the returned task ID with `hermes kanban show <id>`, `runs <id>`,
`log <id>`, `comment <id> "text"`, or `tail <id>`. `watch` and `tail` display
local events until interrupted; they do not dispatch work.

## Core concepts

- **Task:** title, acceptance criteria, assignee, workspace and dependency links.
- **Run:** one claim/attempt with its own process identity, heartbeat and outcome.
- **Profile:** the worker's independent configuration, credentials, model and skills.
- **Workspace:** `scratch`, `worktree`, `worktree:<repo>`, or `dir:<absolute-path>`.
  Explicit scratch artifacts must be preserved before scratch cleanup. Worktrees
  and explicitly selected existing directories are not disposable scratch data.
- **Dispatch:** one bounded claim/reclaim/promote/spawn pass. Concurrent passes
  share a board lock; a losing pass does not duplicate effects.

Ready tasks need an eligible assignee and satisfied dependencies. A blocked task
needs a remedy; inspect its latest run before `unblock` or `promote`. Do not erase
failure counters or delete state just to make a board look healthy.

## Boards (multi-project)

Each board has its own queue, storage and workspaces. Workers are pinned to the
selected board. Select it explicitly when operating across projects:

```sh
hermes kanban boards list
hermes kanban boards create research
hermes kanban --board research list
hermes kanban --board research dispatch --max 1
hermes kanban boards switch research
```

Board export/import is explicit portable data transfer, not live synchronization.
Import creates a new board rather than overwriting an existing one. Export strips
machine-specific claims, process identities and notification subscriptions;
workspaces are not included. Review `hermes kanban boards --help` before transfer
or deletion, and protect exported data like any other user-state export.

## How workers interact with the board

A dispatched worker receives the task/run identity and the dedicated toolset.
It reads `kanban_show`, performs work with its granted tools, records useful
comments and heartbeat progress, then uses a terminal transition:

- `kanban_complete` with concrete results, verification and declared artifacts;
- `kanban_request_review` with a reviewable handoff;
- `kanban_request_changes` for actionable review findings;
- `kanban_block` when a real dependency or human decision is required.

A worker that exits without recording an outcome is not silently marked done.
Claims, run accounting and the failure circuit breaker support bounded recovery.
A provider outage must not cause duplicated side effects or infinite retry loops.

Enable the optional toolset in a conversation profile with
`hermes tools enable kanban --platform cli`. Dispatched workers receive their
worker tools automatically. A normal conversation with board tools is not itself
the owner of every task it can inspect.

### Per-task model override

Profiles provide defaults; a task can pin its worker model/provider:

```sh
hermes kanban create "Implement the focused change" --assignee coder \
  --model <model> --provider <supported-provider>
```

Use a stronger planner/reviewer where useful and a cheaper worker where suitable.
A model name does not bypass provider setup or widen the supported provider catalog.

### Goal-mode cards (`--goal`) {#goal-mode-cards---goal}

`create --goal` gives that worker a bounded continuation/judging loop against its
acceptance criteria. It does not create another queue or an unbounded daemon.
Set `--goal-max-turns` deliberately and keep verification artifacts in the handoff.
See [goals](./goals) for the shared goal engine.

### Auto vs Manual orchestration

The local CLI offers explicit `specify <id>` and `decompose <id>` operations for
turning an idea into an executable specification/dependency graph. Configure their
auxiliary models in encrypted settings. Neither operation starts a service:
inspect the resulting tasks, assign profiles, and dispatch ready work explicitly.

## Review and completion

Dependencies, review status and run identity are distinct. Preserve a structured
handoff with what changed, verification, remaining risks and artifact paths.
A requested review is not completion; `request-changes` must leave actionable
feedback for the next attempt. Inspect `diagnostics --task <id> --json` for blocked
review/dependency relationships rather than manually editing SQLite rows.

`--completion-contract` can require a real publication/PR outcome where the task
calls for one. A textual claim of success does not replace repository identity,
required checks or an actual completed handoff. The owner still controls whether
publishing is authorized.

## `/kanban` slash command {#kanban-slash-command}

Inside the TUI, `/kanban <action>` exposes the same local command surface. For
example, `/kanban list`, `/kanban show <id>`, `/kanban comment <id> "evidence"`,
`/kanban dispatch --dry-run`, and `/kanban boards list`.

Notifications belong to a local TUI session. For explicit subscription:

```sh
hermes kanban notify-subscribe <task-id> --session-id <session-id>
hermes kanban notify-list <task-id> --json
hermes kanban notify-unsubscribe <task-id> --session-id <session-id>
```

`notify` displays a local notice; `notify+wake` also resumes the owning TUI agent;
`wake` resumes it without a separate notice. An omitted mode preserves an existing
subscription's choice. Notifications do not dispatch queued tasks.
Historical remote subscription records remain inspectable/removable, but no
remote transport is installed or re-enabled.

## Concurrency and recovery

Configure host/per-profile caps and the failure limit through `hermes config`.
The dispatcher honors memory pressure, existing workers, review fairness and
per-board claim locking. A missing required process-isolation scope leaves work
queued rather than pretending it started. Never identify a worker by a bare PID;
recovery also checks its recorded creation time.

| Setting | Local behavior |
| --- | --- |
| `kanban.auto_subscribe_on_create` | Subscribe the originating TUI to task events. |
| `kanban.default_assignee` | Fallback assignee for unassigned ready work. |
| `kanban.orchestrator_profile` | Root-task profile chosen by explicit decomposition. |
| `kanban.max_in_progress` | Host-wide running-worker cap; unset derives a memory-aware limit. |
| `kanban.max_in_progress_per_profile` | Optional cap for each profile. |
| `kanban.dispatch_profiles` | Omit to allow existing profiles; an explicit empty list or null allows none. |
| `kanban.failure_limit` | Consecutive failures before blocking; explicit CLI override wins. |
| `kanban.dispatch_stale_timeout_seconds` | Reclaim stale work during a dispatch pass; zero disables it. |
| `kanban.reconcile_orphans` | Recover broken claim bookkeeping during a dispatch pass. |
| `kanban.review_dispatch` | Include assigned review work in local dispatch. |
| `kanban.worker_log_rotate_bytes`, `kanban.worker_log_backup_count` | Worker-log rotation limits. |

For a stalled board:

1. Inspect `list`, `show`, `runs`, `log` and `diagnostics`.
2. Check the assigned profile's provider, model, dependencies and workspace.
3. Correct the cause; use `unblock`/`promote` where appropriate.
4. Run another `dispatch --dry-run`, then a real bounded dispatch.

Preserve encrypted backups before repair or board transfer. Do not delete the
Hermes home or overwrite a damaged DB with an empty board. See
[encrypted state](../encrypted-state) and [installation recovery](../../getting-started/installation).
The canonical command reference is `hermes kanban --help` and each subcommand's
`--help`; implementation details live in `hermes_cli/kanban_*.py`.
