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

## Kanban (multi-agent work queue)

Durable SQLite-backed board letting multiple profiles/workers collaborate. Users: `hermes kanban
<verb>`; dispatcher-spawned workers use a dedicated `kanban_*` toolset so their schema footprint is
zero outside a kanban task (footprint ladder rung 3).

- **CLI:** `hermes_cli/kanban.py` facade + 14 `kanban_*.py` siblings (`boards`, `db`, `db_connect`,
  `db_dispatch`, `db_notify`, `db_graph` (task initialization and decomposition), `workspace`, ...). Verbs: `init, create, list (ls), show, assign, link,
  unlink, comment, attach, attachments, attach-rm, complete, request-review, request-changes,
  reopen-review, block, unblock, archive, tail`, plus `watch, stats, runs, log, assignees, heartbeat,
  notify-*, dispatch, daemon, gc`. Argparse alias dispatch must accept both `list` and `ls` (root).
- **Toolset:** `tools/kanban_tools.py` — `kanban_show, kanban_complete, kanban_request_review,
  kanban_request_changes, kanban_block, kanban_schedule, kanban_heartbeat, kanban_comment,
  kanban_create, kanban_link,
  kanban_attach, kanban_attach_url, kanban_attachments`; platforms whose saved selection enables
  `kanban` (`hermes tools enable kanban --platform <p>`; default-off, in `CONFIGURABLE_TOOLSETS`) get
  the full set plus `kanban_list`/`kanban_unblock` for board routing. The check_fn reads the schema
  build's own selection (`tools/kanban_toolset_context.py`), never the legacy top-level `toolsets`
  key alone.
- **Dispatcher:** long-lived loop (default 60s) that reclaims stale claims, promotes ready tasks,
  atomically claims, and spawns assigned profiles. Runs **inside the gateway** by default
  (`kanban.dispatch_in_gateway: true`). Standalone: `plugins/kanban/systemd/hermes-kanban-dispatcher.service`.
- **Plugin assets:** `plugins/kanban/dashboard/` (web UI) + systemd unit. `kanban_db.connect` is its
  own connection helper — do not alias it to `projects_db.connect` (a path-proximity generator did).

Isolation: **board** is the hard boundary — workers get `HERMES_KANBAN_BOARD` pinned in their env and
cannot see other boards; **tenant** is a soft namespace within a board (workspace-path + memory-key
isolation, one fleet serving several businesses). After `kanban.failure_limit` consecutive
non-success attempts on a task (default 2) the dispatcher auto-blocks it to stop spin loops; a
worker exit of `KANBAN_TERMINAL_PROVIDER_EXIT_CODE` (78 — credential revoked, model gone; the
worker's own `failure_reason` classification via `cli._TERMINAL_PROVIDER_REASONS`) trips it on
the first attempt, sticky, because no retry can heal it (#114587).
Process-identity note: `kanban --preserve-cache` contains "serve" — never classify processes by argv
substring (root). Worker liveness is `(worker_pid, worker_started_at)` — the start-time fingerprint
(`gateway.status.get_process_start_time`) recorded at claim time — never bare PID existence, or a
recycled PID gets killed on reclaim.

- **Notifications leave through the task's owning profile.** `hermes_cli/kanban_db_notify.py`
  subscriptions carry the profile; `gateway/kanban_watchers_notifier.py` delivers via THAT
  profile's adapter under its scope (`_notify_profile_filter`), never the multiplexer's launch
  adapter; a fail-closed skip logs once at WARNING with the remedy, never a bare `continue`.
  Dispatched workers get `HERMES_KANBAN_BOARD` and the assignee's `HERMES_HOME` pinned in a
  scrubbed child env (`build_subprocess_env` + `strip_launch_profile_env`); they never inherit the
  default profile's `.env`.
- **Prompt injection sites gate on ownership, not tool access.** Tool access (`kanban_show` visible
  via a profile's toolset) and an inherited `HERMES_KANBAN_TASK` (delegate children, cron runs beside
  a worker) are not ownership. The kanban guidance (`agent_init`, `system_prompt` fallback) and the
  stop nudge resolve the task via `agent/delegation_context.py::owned_kanban_task()`; other readers
  pair their env read with `is_dispatcher_owned_worker_context()`.
- **Descendant fence is a path, not a flag.** A delegated child's Kanban marker
  (`agent/delegation_context.py::DELEGATED_CHILD_ENV_MARKER`) carries the fenced board ROOT;
  `kanban_path_is_fenced(path)` denies mutations only on the dispatcher-pinned `HERMES_KANBAN_DB`
  or under that root, so a child working against a scratch `HERMES_HOME` keeps a writable board.

## Tests

`tests/cron/`, `tests/hermes_cli/test_kanban*.py`, `tests/tools/test_kanban*.py`. Schedule parsing
and catch-up windows are pure functions — test them as data. Never assert on the verb list or
toolset size (root: no change-detectors). Time-based tests use loose bounds (≥ 2s) and event sync.
