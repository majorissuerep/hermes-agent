# Local Kanban walkthrough

This walkthrough uses only the terminal UI, local CLI and self-called workers.
Configure a `coder` profile and a `reviewer` profile with working providers first.
For concepts and options, see the [Kanban reference](./kanban).

## Start a small task

```sh
hermes kanban init
hermes kanban create "Fix the parser edge case" --assignee coder \
  --body "Reproduce the defect, make a focused fix, and include verification evidence."
```

Record the returned task ID. Inspect it before starting work:

```sh
hermes kanban show <task-id>
hermes kanban dispatch --dry-run
hermes kanban dispatch --max 1
hermes kanban watch
```

A dispatch is one pass, not a background scheduler. `watch` shows events but does
not start work. Run another explicit dispatch when dependent tasks become ready.

## Review the handoff

Use `show`, `runs` and `log` to inspect the worker's outcome. A useful handoff names
the actual changes, verification and durable artifacts. A task that needs review
should enter review, not claim success merely because the model stopped talking.
Assign or create a review task with the implementation task as its parent;
use `hermes kanban create --help` for the dependency/reviewer options.

The reviewer uses `kanban_request_changes` for actionable findings or the normal
completion path after verification. Declared scratch artifacts are preserved before
workspace cleanup; do not depend on an undeclared temporary file surviving.

## Recover a blocked attempt

```sh
hermes kanban list
hermes kanban runs <task-id>
hermes kanban log <task-id>
hermes kanban diagnostics --task <task-id> --json
```

Fix the provider, profile, workspace or dependency named by the failure. Then use
`unblock` or `promote` as appropriate and perform another dry-run/dispatch. Do not
clear the DB, reset failure counters blindly, or repeatedly spend model tokens on
the same uncorrected problem.

For parallel help that belongs only to the current conversation, use
[`delegate_task`](./delegation) instead of a durable board.
