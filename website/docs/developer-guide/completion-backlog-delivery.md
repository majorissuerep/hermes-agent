---
title: Background completion backlogs
sidebar_label: Completion backlogs
---

# Background completion backlogs

Interactive surfaces coalesce consecutive background-process completions that are
already ready for one conversation into a single notification turn. This does not
add a delay or promise to combine jobs that finish at different times. Failures and
successful outputs remain in the batch; one completion keeps its original text.

Process identity remains available until dispatch. Explicit `process_manage`
wait/log/kill consumption can therefore suppress a CLI completion even after it
has left the process registry and entered the input queue. An entirely consumed
batch starts no turn. Watching output and async-delegation results remain separate
notifications, in their original order; they are not folded into completion batches.

## Consumers and ownership

- **Classic CLI:** `hermes_cli/cli_process_notifications.py` owns the idle/post-turn
  drain, compression-aware ownership and final input unwrapping.
- **TUI:** `tui_runtime/session_notifications.py` groups the poller's
  ready snapshot after checking ownership. Each process still emits its own UI
  status. Busy sessions requeue structured events, not rendered batch strings.
- **Post-turn TUI safety net:** `tui_runtime/prompt_turn.py` uses the same routing
  and rendering path. The local Ink client uses this private backend.
- **Noninteractive/headless consumers:** this does not create an autonomous
  notification loop for a one-shot CLI invocation.

The shared renderer is `tools/process_registry_notifications.py::ProcessNotificationBatch`.
Neither a batch nor its delivery status is persisted into the system prompt.
Addressed events still require a provable owner; another live session cannot adopt
them. Delegation delivery continues through its existing durable claim/complete
ledger, once per delegation rather than once per process batch.

## Local validation and its limits

`evals/completion_backlog_probe.py REPO OUTPUT.json` starts real local shell children
in temporary directories, reads their real completion events, and drives the
production CLI, TUI poller and post-turn notification routes. A loopback HTTP turn
sink replaces `chat` / `_run_prompt_submit`; it records actual dispatches but does
**not** exercise model inference, native renderer interaction or a hosted platform.
Synthetic watch and delegation envelopes are labeled fixtures; delegation claims
use the real temporary SQLite ledger. The backlog case includes a nonzero exit.

The probe checks a ready backlog, a single exact payload, explicitly consumed
results, foreign ownership, and watches/delegations interleaved with completions.
It measures turn admission at the notification boundary, not model token savings.
