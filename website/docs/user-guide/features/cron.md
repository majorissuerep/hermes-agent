---
title: Local scheduled jobs
---

# Local scheduled jobs

Hermes stores schedules locally. It does **not** install or run a background scheduler.
To execute due jobs, invoke `hermes cron tick`; to execute a particular job immediately,
invoke `hermes cron run <job-id>`. A system scheduler may invoke the tick command explicitly,
provided it can unlock the owning profile's vault through an approved interactive/session grant.
Never put a vault password in a script, environment file, or service definition.

```bash
hermes cron add --schedule "every 2h" --prompt "Review my local project" --deliver local
hermes cron list
hermes cron tick
hermes cron runs
hermes cron run <job-id>
hermes cron pause <job-id>
hermes cron resume <job-id>
hermes cron remove <job-id>
```

Use `hermes cron add --help` and `hermes cron edit --help` for supported job options.
The agent's `cronjob` tool manages the same store. Creating a job never starts a daemon.
`hermes cron status` reports the local store and upcoming work; `hermes cron doctor` diagnoses
invalid scripts, overdue work and unsupported legacy delivery settings.

## Execution and results

Schedules accept durations (`30m`), every-phrases (`every 2h`), five-field cron expressions
(`0 9 * * *`) and ISO timestamps. Each agent run has its own session. Scripts are files,
not command strings; `no_agent` jobs execute their script without making a model call.
A job may use prior local output with `context_from`, including `self` for continuity.

Results, job definitions and execution history remain local. Output archives are encrypted in
the owning profile's vault and read through Hermes's managed I/O. A locked vault does not fall
back to plaintext. Tick/run retain per-home locking, durable claims and duplicate-occurrence
accounting; interrupted workers with an uncertain outcome are not automatically re-executed.

Only `local` delivery is supported. Old jobs using `origin`, `all`, platform destinations,
Bot Chat, or attached conversations are rejected **before scripts or model calls**. Inspect and
edit these jobs to local results before running them. There are no messaging notifications,
remote scheduler plugins or gateway services to start or restart.

## Vaulted scripts

Migrated Python and shell scripts execute from a temporary, owner-only decrypted copy of the
owning scripts tree. Python `__file__`, imports and shell `$0` identify that execution view,
so sibling-relative assets remain usable. Without an explicit workdir the script's copied
parent is the cwd; an explicit job workdir is preserved exactly. The view is limited to 4096
assets and 16 MiB, rejects symlink escapes, and is removed after success, failure or timeout.
Durable sources stay encrypted; corrupt or plaintext files in a vaulted scripts tree refuse
execution rather than falling back to raw bytes. A vault unlock is not requested again.
