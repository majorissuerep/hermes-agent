# Local delegation and background work

There is no gateway, remote scheduler or remote worker host. Every execution belongs
to an explicit local profile and owns observable process/session state.

## Delegation

`delegate_task` creates isolated agent contexts and terminal sessions. A task array
runs children concurrently within `delegation.max_concurrent_children`; depth and
iteration limits remain enforced. Background delegation returns a handle and later
injects its result into the parent's conversation.

Children reuse revocable local session authority for encrypted state and native
Hermes commands. Never give them a passphrase or raw key. Closing the owner revokes
its authority. Background delegation is process-local, not a durable service.

## Cron

Use `hermes cron --help`, the `/cron` command or the `cronjob` tool to manage durable
jobs. Drive execution explicitly:

```sh
hermes cron list
hermes cron tick
hermes cron run JOB_ID
hermes cron runs JOB_ID
```

There is no installed scheduler service. Results stay local and encrypted; remote
delivery targets are refused before scripts or models run. Script-only and monitored
jobs retain execution accounting, crash recovery, timeout and duplicate-tick guards.

Scripts migrated into encrypted state are decrypted only into a bounded private
execution view; sibling imports/data remain available and the view is removed after
execution. Do not execute ciphertext or create an uncontrolled plaintext fallback.
An unattended run requires explicitly configured unlock authority; a run inside an
unlocked session reuses its scoped capability.

## Kanban and terminal processes

`hermes kanban --help` provides a local durable board and dispatcher-spawned terminal
workers. Claims, owner identity, heartbeat, completion and retries are recorded.
There is no dispatch-in-gateway setting. A selected board/profile is an isolation
boundary, not an invitation to guess another process's identity from its argv.

`terminal(background=true)` is a local managed process. Use its process handle and
status instead of assuming an exit or log line proves success. Persisted child work
must explicitly outlive its parent and retain identity-bound cleanup/accounting.

## Curator

`hermes curator --help` maintains agent-created skills with backups, pinning, archive
and rollback. Bundled/user-installed skills are not arbitrary cleanup targets.
Aux-model consolidation is opt-in. Routine deterministic maintenance must not spend
model tokens or mutate an active conversation's prompt prefix.
