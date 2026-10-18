---
title: Local cron internals
description: Local schedules, explicit execution, encrypted results, and durable ownership
---

# Local cron internals

Cron is an explicit local executor, not a service. `hermes cron tick` executes due jobs once;
`hermes cron run <id>` claims a manual fire. The `cronjob` tool uses the same store and runner.
No gateway, messaging adapters, hosted scheduler providers or background timer are installed.

## Ownership and execution

`cron/jobs.py` owns job persistence and schedule mutation; `cron/scheduler.py` owns execution;
its siblings isolate prompt preparation, bounded scripts, tick locking and bookkeeping.
Schedules are durations, every-phrases, five-field cron expressions or ISO one-shots.
All state belongs to an explicit profile home; copied thread contexts and native workers retain
that home and its secret scope rather than reading the launch profile's globals.

A per-home file lock excludes concurrent ticks. Recurring schedules advance before dispatch,
with a persisted pending slot. Durable fire claims and execution rows fence the run, heartbeat
its ownership and prevent a completed scheduled instant from being replayed after a job-store
rollback. Lost ownership prevents stale output and bookkeeping. A manual forced run is not a
completion of a different pending scheduled instant.

The direct runner acquires the local SessionDB with a bounded wait and fails closed if it cannot
persist the conversation. Agent jobs preserve the normal configured memory policy. Scripts run
with a separate timeout; agent inactivity uses progress rather than total wall time. Job cwd is
scoped to its task and never changes the process-wide terminal cwd.

## Native worker handoff

When a short-lived Linux systemd invocation needs a worker that outlives its parent, the existing
process-registry API selects a user scope. Without that requirement the run stays in-process.
A required unavailable scope fails closed; an explicitly allowed degraded child is not reported
as isolated. PM workers lease their committed dependency generation before imports.

The parent seals the payload using `hermes_security.io` and offers an execution-scoped,
exact-home session grant. The worker adopts its in-memory vault key before reading the payload,
then adopts the durable execution row and publishes its ready acknowledgement atomically.
The grant remains available through this handoff; the worker owns its authority for local child
processes. No master password is persisted. Failed adoption performs no execution and removes
the payload. An uncertain adopted worker outcome is recorded rather than blindly replayed.

## Results and legacy jobs

Output archives use the existing vault I/O with purpose `state`; continuity and interactive
completion readers decrypt through that same boundary. Raw plaintext fallback is forbidden.
Failures remain inspectable in execution history and incidents; successful runs resolve open
incidents. There is no outbound delivery queue or acknowledgement channel.

Only `deliver=local` and local failure output are supported. A stored retired destination
(`origin`, `all`, platform routes, Bot Chat or attached sessions) is rejected before scripts,
prompt preparation or model calls, independently of optional preflight configuration. Old jobs
can still be inspected, paused and edited to local operation. Declarative hooks register against
the explicit owning home; interactive async completion remains process-local, not durable routing.

## Vaulted scripts

Migrated Python and shell scripts execute from a temporary, owner-only decrypted copy of the
owning scripts tree. Python `__file__`, imports and shell `$0` identify that execution view,
so sibling-relative assets remain usable. Without an explicit workdir the script's copied
parent is the cwd; an explicit job workdir is preserved exactly. The view is limited to 4096
assets and 16 MiB, rejects symlink escapes, and is removed after success, failure or timeout.
Durable sources stay encrypted; corrupt or plaintext files in a vaulted scripts tree refuse
execution rather than falling back to raw bytes. A vault unlock is not requested again.
