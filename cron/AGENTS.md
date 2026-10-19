# cron/ (+ kanban) — scheduled jobs and the multi-agent work queue

Applies on top of the root `AGENTS.md`. Long-form: `website/docs/developer-guide/cron-internals.md`;
user docs `website/docs/user-guide/features/cron.md`, `kanban.md`.

## Cron

`cron/jobs.py` owns the encrypted job store; `cron/scheduler.py` and its siblings execute local
jobs. Users invoke `hermes cron tick` to run due jobs once, or `hermes cron run <id>` explicitly.
The `cronjob` tool creates and manages the same jobs. There is no background service, hosted
scheduler, messaging transport, or automatic boot-time ticker. Creating a schedule does not start
execution; callers must invoke tick/run. Interactive async completion is process-local only.

- Vaulted scripts run in a bounded owner-only temporary execution view, preserving sibling-relative
  assets and explicit workdir; clean it on every exit and never execute ciphertext or plaintext
  fallback in a vaulted tree.
- Results and continuity archives use `hermes_security.io` with purpose `state`. Never read or
  write archived output through raw text I/O; a locked or missing vault fails closed.
- Only `deliver=local` and local failure results are supported. Reject retired destinations and
  `attach_to_session` before scripts, prompt preparation, provider calls, or model spend, even when
  optional config preflight is disabled. Old stored remote jobs remain inspectable/pausable but
  cannot execute until edited to local.
- Schedules support durations, every-phrases, five-field cron and ISO one-shots. Scripts are files
  under the owning home's scripts directory, never command strings. `no_agent` runs only the
  script; `context_from` reads managed local result archives; workdir binds task-local cwd.
- Preserve per-home tick locking, schedule CAS, durable fire claims, claim heartbeats, execution
  ledger accounting, and scheduled-occurrence duplicate suppression. Advance recurring instants
  before dispatch and recover a dead owner's pending slot once; never repeat completed effects.
- Preserve the inactivity watchdog and bounded script timeout. A long active job is not an idle
  job. Lost claim ownership fences output and bookkeeping; stale workers cannot overwrite a newer
  fire. A missing local SessionDB is fatal, not permission to run unpersisted inference.
- Profile home and secret scope are explicit across copied contexts, threads and child processes.
  Running sets and parallel pools remain keyed by home as well as job ID. Release claims under
  the home key used to register them.
- Restart-safe worker handoff retains durable adoption/acknowledgement, PM generation lease,
  process identity and bounded recovery. A worker with an uncertain outcome is not blindly retried.
- Cron sessions retain the normal configured memory policy and use synchronous child delegation so completion cannot leak into
  an unrelated interactive session. Declarative hooks register against the explicit owning home.

## Kanban (local multi-agent work queue)

`hermes_cli/kanban.py` dispatches finite CLI/slash operations; its siblings own
board storage, task transitions, diagnostics, workspaces and worker launch.
`hermes kanban dispatch` performs one explicit pass. There is no gateway ticker,
background service or daemon command. Creating a task does not start workers.

- Boards are independent queues. Workers receive the selected board and the
  assignee's profile home explicitly, through the existing scrubbed child env.
  Shared boards never imply shared profile credentials.
- Preserve atomic claims, per-board dispatch locking, host/per-profile concurrency
  caps, review-lane fairness, failure accounting, backoff, and PID/start-time
  identity. An unavailable required execution scope leaves work ready; never
  charge a task for infrastructure that never started it.
- The dedicated `kanban_*` toolset lets an owning worker mutate its task directly.
  An inherited task ID alone does not establish ownership. Delegated children and
  adjacent cron runs retain the existing board/path fences and cannot complete
  their parent's task.
- Notifications are local TUI subscriptions owned by an exact session/profile.
  Historical remote rows remain readable/removable but have no transport.
- Keep review handoffs, attachments, dependency graphs, goal-mode budgets,
  checkpoints and terminal outcomes durable. Declared scratch artifacts must be
  preserved before workspace cleanup. Never delete unrelated worktrees.
- State and scripts use the owning profile's encrypted I/O/SQLCipher paths.
  Bind profile/secret context across threads and child spawns.

## Tests

Use `scripts/run_tests.sh` with isolated homes: `tests/cron/`,
`tests/hermes_cli/test_kanban*.py`, and `tests/tools/test_kanban*.py`.
Keep behavioral claim/recovery/ownership contracts, not verb-list snapshots.
Actual worker lifecycle qualification runs in disposable containers on the target
OS; skipped native cases are not evidence for that OS.
