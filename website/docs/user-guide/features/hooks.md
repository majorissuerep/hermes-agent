---
sidebar_position: 6
title: "Event Hooks"
description: "Run custom code at key lifecycle points — log activity, send alerts, post to webhooks"
---

# Event Hooks

Hermes has four hook systems that run custom code at key lifecycle points:

| System | Registered via | Runs in | Use case |
|--------|---------------|---------|----------|
| **[Plugin hooks](#plugin-hooks)** | `ctx.register_hook()` in a [plugin](./plugins.md) | CLI + TUI | Tool interception, metrics, guardrails |
| **[Shell hooks](#shell-hooks)** | `hooks:` block in profile `config.yaml` pointing at shell scripts | CLI + TUI | Drop-in scripts for blocking, auto-formatting, context injection |
| **[Outbound webhooks](#outbound-webhooks)** | `hooks.outbound:` list in `~/.hermes/config.yaml` | CLI + TUI | Push signed lifecycle events to external HTTP endpoints — CI, dashboards, other agents |

Hook callback errors are isolated and logged rather than crashing the agent. Hooks are not all passive: directive/control hooks can change flow, transforms can replace content, and a shell `pre_tool_call` hook can block or fail closed.

## Plugin Hooks

[Plugins](./plugins.md) can register hooks that fire in **CLI and TUI** sessions. These are registered programmatically via `ctx.register_hook()` in your plugin's `register()` function.

For plugin packaging and registration details, see
the [Plugins guide](./plugins.md).

```python
def register(ctx):
    ctx.register_hook("pre_tool_call", my_tool_observer)
    ctx.register_hook("post_tool_call", my_tool_logger)
    ctx.register_hook("pre_llm_call", my_memory_callback)
    ctx.register_hook("post_llm_call", my_sync_callback)
    ctx.register_hook("on_session_start", my_init_callback)
    ctx.register_hook("on_session_end", my_cleanup_callback)
    # Kanban board lifecycle (dependency-wait blocking may fire inside its transaction):
    ctx.register_hook("kanban_task_claimed", my_claim_callback)     # dispatcher process
    ctx.register_hook("kanban_task_completed", my_done_callback)    # worker process
    ctx.register_hook("kanban_task_blocked", my_blocked_callback)   # worker process
```

**General rules for all hooks:**

- Callbacks receive **keyword arguments**. Always accept `**kwargs` for forward compatibility.
- Callback exceptions are logged and skipped; later callbacks continue. A callback that fails the same way on every call (typically a signature naming a field the hook does not send, e.g. `tool_data` instead of `tool_name`/`args`) is reported **once** at WARNING — the message lists the fields the hook provides — and identical repeats go to DEBUG, so a mis-declared plugin cannot flood the log.
- If a Python plugin callback on a **timeout-bounded** hook (hot-path observers such as `post_tool_call` / `pre_llm_call`, plus the policy hook `pre_tool_call`) **blocks** longer than `plugins.hook_callback_timeout` (default 30s, set `0` to disable, max 600), it is abandoned without joining the worker so the agent loop continues. Timed-out or still-running `pre_tool_call` callbacks **fail closed** (block the tool); other bounded hooks fail open (skip). Hooks with a documented caller-thread contract (`subagent_stop`) are never moved onto a timeout worker. Shell hooks keep their own per-entry `timeout`.
- The catalog below is descriptive: **observers** ignore returns, **transforms** accept the first valid string replacement, and **directive/control** hooks consume documented return shapes. Plugin middleware is a separate registry and surface, not another hook category.
- Correlation fields such as `turn_id`, `api_request_id`, `task_id`, `session_id`, and `api_call_count` are hook-specific and may be absent. Treat IDs as opaque.
- Runtime event-name validity comes from `hermes_cli.plugins.VALID_HOOKS`. `hermes hooks list` lists configured shell/outbound hooks, not every available event; `hermes hooks test <event>` reports the valid set only when an invalid event is supplied.

### Cache-safe system prompt sections

Plugins that need durable, always-on guidance can register a bounded system
prompt section instead of injecting the same text through `pre_llm_call` on
every turn:

```python
def board_rules(session_info):
    return f"Apply the worker rules for profile {session_info['profile_name']}."

def register(ctx):
    ctx.register_system_prompt_section(
        "kanban-advanced.worker-rules",
        board_rules,                       # a string is also accepted
        position="after_memory",
        max_chars=4000,
    )
```

The contract is deliberately narrow:

- IDs are global, stable, 1–128 character lowercase identifiers using only
  letters, numbers, `.`, `_`, and `-`. Duplicate IDs are rejected.
- `after_memory` is the only placement anchor. Sections are sorted by ID,
  rendered after memory/profile context and before session metadata; plugins
  cannot reorder or replace core prompt content.
- A callable receives a read-only mapping with `session_id`, `model`,
  `provider`, `platform`, `profile_name`, and `cwd`. It runs **once for a new
  session**. Its rendered bytes are frozen on compression and recovered from
  the already-persisted full system prompt after a process restart/resume;
  plugin state is not re-read for an existing session.
- `max_chars` is capped at 4,000 characters. All plugin sections together,
  including their audit headings, are capped at 8,000 characters and 32
  sections. Empty, non-string, oversized, aggregate-over-budget, or raising
  sections are skipped with a warning; prompt construction continues.
- Every accepted section is named in the prompt and logged at session start
  with its plugin, position, and character count.

Use `pre_llm_call` for truly dynamic per-turn context. There is intentionally
no plugin environment-hints hook in this contract: changing cwd, branch, or
other environment data must not silently mutate a session's cached prompt.
Such a hook needs a concrete consumer and the same frozen/resume-safe semantics
before it can be added.

### Shipped plugin-hook catalog

Payload fields below are the exact event-specific fields supplied by each call site. For backward compatibility, `PluginManager` also adds `telemetry_schema_version="hermes.observer.v1"` to every plugin-hook callback. That legacy envelope marker does not mean all hook payloads share one semantic schema; new versioned contracts belong to their concrete event or capability family.

| Hook | Category | Exact timing and return behavior | Explicit payload fields | Privacy / sensitivity |
|---|---|---|---|---|
| [`pre_tool_call`](#pre_tool_call) | Directive/control | Once before execution; any valid `block` wins over any `approve` (then the first valid `approve`), and `modify` returns are shallow-merged into the tool arguments. | `tool_name`, `args`, `task_id`, `session_id`, `tool_call_id`, `turn_id`, `api_request_id`, `middleware_trace` | Raw arguments may contain user content, paths, commands, or secrets. |
| `post_tool_call` | Observer | After blocked, error, or successful result; return ignored. | `tool_name`, `args`, `result`, `task_id`, `session_id`, `tool_call_id`, `turn_id`, `api_request_id`, `duration_ms`, `status`, `error_type`, `error_message`, `middleware_trace` | Result/error text may contain arbitrary tool or user content and secrets. |
| `transform_tool_result` | Transform | After `post_tool_call`, before conversation append; first string replaces the result. | `tool_name`, `args`, `result`, `task_id`, `session_id`, `tool_call_id`, `turn_id`, `api_request_id`, `duration_ms`, `status`, `error_type`, `error_message` | Exposes the full model-bound result and arguments. |
| `transform_terminal_output` | Transform | After bounded foreground process capture, before final output limiting; first string replaces output. | `command`, `output`, `returncode`, `task_id`, `env_type` | Command/output may contain credentials. |
| `pre_transcription` | Transform | Fired by the STT dispatcher after provider resolution and before any backend (built-in, command-type, or plugin-registered) is invoked; dict results are applied in registration order, last-writer-wins per field (`prompt`, `language`, `model`; `file_path` is read-only). | `file_path`, `provider`, `model`, `language`, `prompt`, `source` | The final prompt is uploaded to the configured STT provider with the audio — keep secrets out of hook returns. |
| `pre_llm_call` | Directive/control | Once per turn before the loop; all valid string/`{"context": ...}` returns are joined and injected into the user message. | `session_id`, `task_id`, `turn_id`, `user_message`, `conversation_history`, `is_first_turn`, `model`, `platform`, `parent_session_id`, `sender_id` | Full user message and conversation history. |
| `post_llm_call` | Observer | Successful, non-interrupted turn finalization; return ignored. | `session_id`, `task_id`, `turn_id`, `user_message`, `assistant_response`, `conversation_history`, `model`, `platform` | Full prompt, response, and history. |
| `transform_llm_output` | Transform | Before `post_llm_call` and final delivery; first non-empty string replaces the response. | `response_text`, `session_id`, `model`, `platform` | Full final assistant text. |
| `pre_verify` | Directive/control | At the bounded edited-code verify gate; first valid continue/block-stop directive keeps the turn going. | `session_id`, `platform`, `model`, `coding`, `attempt`, `final_response`, `changed_paths` | Draft response and changed paths. |
| `pre_api_request` | Observer | Per provider attempt, immediately before the request; return ignored. | `task_id`, `turn_id`, `api_request_id`, `session_id`, `user_message`, `conversation_history`, `platform`, `model`, `provider`, `base_url`, `api_mode`, `api_call_count`, `retry_count`, `request_messages`, `message_count`, `tool_count`, `approx_input_tokens`, `request_char_count`, `max_tokens`, `started_at`, `middleware_trace`, `request` | High sensitivity: legacy `user_message`, `conversation_history`, and `request_messages` are intentionally raw; prefer sanitized `request`. |
| `post_api_request` | Observer | After normalized provider success; return ignored. | `task_id`, `turn_id`, `api_request_id`, `session_id`, `platform`, `model`, `provider`, `base_url`, `api_mode`, `api_call_count`, `api_duration`, `started_at`, `ended_at`, `finish_reason`, `message_count`, `response_model`, `response`, `usage`, `assistant_message`, `assistant_content_chars`, `assistant_tool_call_count` | Sanitized `response` is available, but raw normalized `assistant_message` may contain model/user content; `usage` is accounting data. |
| `api_request_error` | Observer | On each failed provider attempt; return ignored. | `task_id`, `turn_id`, `api_request_id`, `session_id`, `platform`, `model`, `provider`, `base_url`, `api_mode`, `api_call_count`, `api_duration`, `started_at`, `ended_at`, `status_code`, `retry_count`, `max_retries`, `retryable`, `reason`, `error`, `request` | Error text may contain provider/user data; `request` is intended to be sanitized. |
| `pre_auxiliary_call` | Observer | Per provider attempt of an auxiliary LLM call (titling, compression, MoA, vision, approval, ...), immediately before the request; return ignored. | `aux_task`, `task_id`, `turn_id`, `session_id`, `platform` (the parent turn's, empty outside a turn), `api_request_id`, `api_call_count`, `retry_count`, `streaming`, `model`, `provider`, `base_url`, `api_mode`, `request_messages`, `system_prompt`, `message_count`, `tool_count`, `approx_input_tokens`, `request_char_count`, `max_tokens`, `started_at`, `request` | `request_messages` is raw (compression sees the whole transcript); prefer sanitized `request`. |
| `post_auxiliary_call` | Observer | After the same attempt returns or raises; return ignored. | `pre_auxiliary_call` identity fields plus `api_duration`, `ended_at`, `finish_reason`, `response_model`, `usage`, `response`, `assistant_content_chars`, `assistant_tool_call_count`, `error`, `error_type` (`None` on success; `usage`/`response` are `None` on error and for `streaming=True`) | Sanitized `response`; `usage` is accounting data; `error` may contain provider text. |
| `on_stream_start` | Observer | Dispatched when a streaming LLM response begins; delivered off the token path via a host-owned bounded queue with one worker per callback; return ignored. | `turn_id`, `iteration`, `session_id`, `model`, `provider`, `surface` | Identifiers and routing metadata only. |
| `on_stream_delta` | Observer | Dispatched per normalized streaming text delta via the bounded observer queue; a stalled callback drops only its own oldest events; return ignored. | `delta`, `kind` (`text` or `reasoning`), `turn_id`, `iteration`, `session_id`, `model`, `provider`, `surface` | Delta text is raw model output; reasoning deltas require the `plugins.stream_reasoning_deltas` opt-in. |
| `on_stream_end` | Observer | Dispatched when a streaming response finishes or errors, after the stream closes; return ignored. | `final_text`, `finished`, `error`, `turn_id`, `iteration`, `session_id`, `model`, `provider`, `surface` | Full assembled response text; error text may include provider data. |
| `on_interim_message` | Observer | Dispatched when a mid-loop assistant message is surfaced before the final answer (streaming or non-streaming); return ignored. | `text`, `already_streamed`, `turn_id`, `iteration`, `session_id`, `model`, `provider`, `surface` | Full interim assistant text. |
| `transform_api_error_classification` | Transform | On each failed provider attempt, at the top of the built-in classifier; all callbacks run, then the first dict with a valid `reason` wins (run-all-then-pick-first), and skipped valid results log a runtime warning. Python plugins only. | `provider`, `model`, `status_code`, `error_type`, `error_code`, `error_message`, `error_body`, `error`, `approx_tokens`, `context_length`, `num_messages` | `error_message` and `error_body` may contain raw provider/user data. |
| `on_session_start` | Observer | First turn of a new session; return ignored. | `session_id`, `model`, `platform` | Identifiers and routing metadata only. |
| `on_session_end` | Observer | Canonically at each turn finalization; CLI/TUI exits have additional reduced legacy shapes. Return ignored. | Canonical: `session_id`, `task_id`, `turn_id`, `completed`, `failed`, `interrupted`, `turn_exit_reason`, `model`, `platform`; exit paths may add `reason`/`api_request_id` and omit fields. | IDs, model/platform, and outcome; canonical payload has no message body. |
| `on_session_finalize` | Observer | CLI/TUI/gateway teardown through `finalize_session`; gateway shutdown may finalize without a reset. Return ignored. | Surface-dependent `session_id`, `platform`, optionally `reason`, `old_session_id`, `new_session_id` | Session and routing identifiers. |
| `on_session_reset` | Observer | CLI/TUI session boundary and gateway after the replacement session exists; return ignored. | CLI: `session_id`, `platform`, `reason`; TUI: `session_id`, `platform`; gateway: those plus `reason`, `old_session_id`, `new_session_id` | Session and routing identifiers. |
| `agent_loop_stopped` | Observer | Immediately after a real running agent is interrupted — gateway `_interrupt_and_clear_session` or TUI/desktop `session.interrupt`; return ignored. | `session_key`, `platform`, `reason`, `invalidation_reason` | Session/routing identifiers and interruption reasons; no message body. |
| `on_skill_lifecycle` | Observer | After an authoritative skill-usage state change; return ignored. | `action`, `skill_name`, `provenance`, `task_id`, `session_id`, `use_count`, `reused`, `reuse_after_patch` | Exposes the local skill name and provenance. |
| `subagent_start` | Observer | Child constructed and about to run; return ignored. | `parent_session_id`, `parent_turn_id`, `parent_subagent_id`, `child_session_id`, `child_subagent_id`, `child_role`, `child_goal` | Child goal may contain user/project content. |
| `subagent_stop` | Observer | Child exit; return ignored. | `parent_session_id`, `parent_turn_id`, `child_session_id`, `child_role`, `child_summary`, `child_status`, `tool_call_history`, `duration_ms` | Summary and redacted tool-history metadata may reveal project structure. |
| `pre_command` | Observer | Recognized slash command about to be dispatched, before the handler runs, on CLI and gateway cold-path dispatch; return ignored in v1 (directive-shaped dicts are logged at debug). Gateway running-agent intercept commands (`/stop`, `/approve` during an active run) are deliberately excluded — control-plane escape hatches must stay outside plugin reach. | `surface` (`"cli"` \| `"gateway"`), `command` (canonical name), `alias_used`, `args_raw`, `session_key`, `platform` | `args_raw` may contain user content or secrets typed after the command. |
| `pre_approval_request` | Observer | Before prompted or smart approval; return ignored. | `command`, `description`, `pattern_key`, `pattern_keys`, `session_key`, `surface`, `turn_id`, `tool_call_id` | Command may contain secrets; smart observer preparation force-redacts, but surfaces do not all have identical redaction. |
| `post_approval_response` | Observer | After a decision, timeout, or gateway notification failure; return ignored. | `command`, `description`, `pattern_key`, `pattern_keys`, `session_key`, `surface`, `turn_id`, `tool_call_id`, `choice`; smart path may add `decided_by` | Same command sensitivity plus decision metadata. |
| `on_human_input_request` | Observer | The agent is about to block on a person — sudo password prompt, `clarify` question, or dangerous-command approval — on CLI, Ink TUI/Desktop, ACP and gateway platforms; return ignored. | `kind` (`"sudo"` \| `"clarify"` \| `"approval"`), `request_id`, `session_id`, `session_key`, `platform`, `prompt` | `prompt` is always force-redacted; the typed password or answer is never passed. |
| `on_human_input_resolved` | Observer | Exactly once per `on_human_input_request`, when the wait ends (answered, skipped, timed out, withdrawn, or failed); return ignored. | Same as the request plus `outcome` | Outcome only — never the answer itself. |
| `kanban_task_claimed` | Observer | After claim commit, in dispatcher process before worker spawn; return ignored. | `task_id`, `profile_name`, `board`, `assignee`, `run_id` | Board/task/profile/assignee identifiers. |
| `kanban_task_completed` | Observer | After completion and cleanup, usually in worker process; return ignored. | `task_id`, `profile_name`, `board`, `assignee`, `run_id`, `summary` | Summary may contain project/user content. |
| `kanban_task_blocked` | Observer | After a blocked transition; the dependency-wait path fires before its transaction exits. Return ignored. | `task_id`, `profile_name`, `board`, `assignee`, `run_id`, `reason` | Reason may contain project/user content. |
| `on_kanban_worker_spawned` | Observer | After `spawn_fn` returns and the worker PID is persisted; runs inside the dispatch lock, keep callbacks fast. Return ignored. | `task_id`, `profile_name`, `board`, `assignee`, `run_id`, `worker_pid`, `workspace_path` | `workspace_path` is a filesystem path and may reveal project layout or usernames. |
| `on_kanban_worker_exited` | Observer | Tick-derived: after `detect_crashed_workers` reclaims a dead-PID task and the reclaim commits. Return ignored. | `task_id`, `profile_name`, `board`, `assignee`, `run_id`, `worker_pid`, `exit_kind`, `exit_code`, `outcome`, `retry_status` | Identifiers and exit metadata only. |
| `on_kanban_worker_stale_claim` | Observer | After a TTL-expired claim is reclaimed; live-PID extensions don't fire. Return ignored. | `task_id`, `profile_name`, `board`, `assignee`, `run_id`, `worker_pid`, `heartbeat_stale`, `retry_status` | Identifiers and claim metadata only. |
| `on_kanban_task_updated` | Observer | After a committed task-field write outside the claim/complete/block lifecycle (assign, overrides, dashboard editors). Return ignored. | `task_id`, `profile_name`, `board`, `assignee`, `run_id`, `changed_fields` | `changed_fields` carries field names only, never values; the named title/body values in the board DB may contain user/project content. |
| `on_kanban_dispatch_tick` | Observer | Once per dispatcher tick, strictly after the dispatch lock is released; idle and contended ticks fire too. Return ignored. | `board`, `profile_name`, `dry_run`, `outcome`, `result` | `result` is the tick's `DispatchResult` and carries task ids, assignees, and workspace paths. |

---

### Streaming output hooks

These observer-only hooks let plugins consume streaming LLM output for telemetry, live dashboards, or TTS pipelines without changing the response. They are delivered through host-owned bounded queues with one background worker per registered callback, so plugin callbacks never run inline on the token path. If one callback stalls, only that callback's queue can fill and drop its oldest pending observer event; other observers continue receiving events independently.

Register them like any other plugin hook:

```python
def on_delta(delta, kind, model, provider, **kwargs):
    if kind == "text":
        print(delta, end="", flush=True)

def register(ctx):
    ctx.register_hook("on_stream_delta", on_delta)
```

Common fields for all four hooks:

| Parameter | Type | Description |
|-----------|------|-------------|
| `turn_id` | `str` | Opaque turn identifier, when available |
| `iteration` | `int` | Current API-call/tool-loop iteration |
| `session_id` | `str` | Current Hermes session id |
| `model` | `str` | Active model identifier |
| `provider` | `str` | Active provider name |
| `surface` | `str` | Calling surface, e.g. `cli`, `discord`, `telegram` |

Additional fields:

| Hook | Extra fields |
|------|--------------|
| `on_stream_start` | none |
| `on_stream_delta` | `delta: str`, `kind: "text" | "reasoning"` |
| `on_stream_end` | `final_text: str`, `finished: bool`, `error: str | None` |
| `on_interim_message` | `text: str`, `already_streamed: bool` |

`on_interim_message` can also fire after a non-streaming response, so registering only that hook does not force a provider call onto streaming transport.

Reasoning deltas are not exposed to plugins by default. Opt in explicitly:

```yaml
plugins:
  stream_reasoning_deltas: true
```

Return values are ignored. To keep the stream fast, callbacks should enqueue their own work and return quickly. Exceptions are logged and do not stop the stream.

---

### `pre_tool_call`

Fires **immediately before** every tool execution — built-in tools and plugin tools alike.

**Callback signature:**

```python
def my_callback(tool_name: str, args: dict, task_id: str, **kwargs):
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `tool_name` | `str` | Name of the tool about to execute (e.g. `"terminal"`, `"web_search"`, `"read_file"`) |
| `args` | `dict` | The arguments the model passed to the tool |
| `task_id` | `str` | Session/task identifier. Empty string if not set. |

**Fires:** In `model_tools.py`, inside `handle_function_call()`, before the tool's handler runs. Fires once per tool call — if the model calls 3 tools in parallel, this fires 3 times.

**Return value — block or require approval:**

```python
return {"action": "block", "message": "Reason the tool call was blocked"}
# or
return {"action": "approve", "message": "Why approval is required", "rule_key": "optional:scope"}
```

Precedence is `block` > `approve` > no directive, regardless of registration order: any plugin's valid `block` wins over an earlier plugin's `approve`, and among `approve` directives the first valid one wins (Python plugins are registered first, then shell hooks). `block` requires a non-empty `message` and short-circuits the tool with that text as the error returned to the model. `approve` escalates the call to the existing human-approval gate; `message` and `rule_key` are optional, and denial, timeout, or gate error fails closed. Other return values are ignored, so existing observer-only callbacks keep working unchanged.

**Return value — rewrite the tool's arguments:**

```python
return {"action": "modify", "args": {"new_string": "fixed content"}}
```

The returned `args` dictionary is shallow-merged over the original tool arguments before the tool executes. Multiple `modify` hooks accumulate — each hook's keys are merged into one accumulated dict built from the original args, so hook A changing `path` and hook B changing `content` both survive. If two hooks modify the same key, the later hook wins.

Shell hooks also accept the Claude Code-compatible format:

```json
{"decision": "modify", "tool_input": {"new_string": "fixed content"}}
```

Both formats are normalized internally to `{"action": "modify", "args": {...}}`.

If a `pre_tool_call` callback exceeds `plugins.hook_callback_timeout` (or is still running from a previous timed-out fire), Hermes **fails closed**: the tool is blocked with a timeout message rather than proceeding without a policy decision. The same applies to a callback that raises: the block message names the callback and the error. A hung callback is skipped for a 60s suppression window; after that a new tool call runs it again (up to three abandoned workers per callback, so a permanently hung plugin blocks tool calls with a warning naming it instead of silently wedging the agent until restart).

**Use cases:** Logging, audit trails, tool call counters, blocking dangerous operations, rate limiting, per-user policy enforcement, argument sanitization, path rewriting, injecting default parameters.

**Example — tool call audit log:**

```python
import json, logging
from datetime import datetime

logger = logging.getLogger(__name__)

def audit_tool_call(tool_name, args, task_id, **kwargs):
    logger.info("TOOL_CALL session=%s tool=%s args=%s",
                task_id, tool_name, json.dumps(args)[:200])

def register(ctx):
    ctx.register_hook("pre_tool_call", audit_tool_call)
```

**Example — warn on dangerous tools:**

```python
DANGEROUS = {"terminal", "write_file", "patch"}

def warn_dangerous(tool_name, **kwargs):
    if tool_name in DANGEROUS:
        print(f"⚠ Executing potentially dangerous tool: {tool_name}")

def register(ctx):
    ctx.register_hook("pre_tool_call", warn_dangerous)
```

---

### `post_tool_call`

Fires **immediately after** every tool execution returns.

**Callback signature:**

```python
def my_callback(tool_name: str, args: dict, result: str, task_id: str,
                duration_ms: int, **kwargs):
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `tool_name` | `str` | Name of the tool that just executed |
| `args` | `dict` | The arguments the model passed to the tool |
| `result` | `str` | The tool's return value (always a JSON string) |
| `task_id` | `str` | Session/task identifier. Empty string if not set. |
| `duration_ms` | `int` | How long the tool's dispatch took, in milliseconds (measured with `time.monotonic()` around `registry.dispatch()`). |

**Fires:** In `model_tools.py`, inside `handle_function_call()`, after the tool's handler returns. Fires once per tool call. Does **not** fire if the tool raised an unhandled exception (the error is caught and returned as an error JSON string instead, and `post_tool_call` fires with that error string as `result`).

**Return value:** Ignored.

**Use cases:** Logging tool results, metrics collection, tracking tool success/failure rates, latency dashboards, per-tool budget alerts, sending notifications when specific tools complete.

**Example — track tool usage metrics:**

```python
from collections import Counter, defaultdict
import json

_tool_counts = Counter()
_error_counts = Counter()
_latency_ms = defaultdict(list)

def track_metrics(tool_name, result, duration_ms=0, **kwargs):
    _tool_counts[tool_name] += 1
    _latency_ms[tool_name].append(duration_ms)
    try:
        parsed = json.loads(result)
        if "error" in parsed:
            _error_counts[tool_name] += 1
    except (json.JSONDecodeError, TypeError):
        pass

def register(ctx):
    ctx.register_hook("post_tool_call", track_metrics)
```

---

### `pre_llm_call`

Fires **once per turn**, before the tool-calling loop begins. All valid callback returns are aggregated in plugin order and injected into the current turn's user message.

**Callback signature:**

```python
def my_callback(session_id: str, user_message: str, conversation_history: list,
                is_first_turn: bool, model: str, platform: str, **kwargs):
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `session_id` | `str` | Unique identifier for the current session |
| `user_message` | `str \| list` | The user's original message for this turn (before any skill injection). A multimodal turn (image or other attachment) is the list of content parts, exactly as sent |
| `conversation_history` | `list` | Copy of the full message list (OpenAI format: `[{"role": "user", "content": "..."}]`) |
| `is_first_turn` | `bool` | `True` if this is the first turn of a new session, `False` on subsequent turns |
| `model` | `str` | The model identifier (e.g. `"anthropic/claude-sonnet-4.6"`) |
| `platform` | `str` | Where the session is running: `"cli"`, `"telegram"`, `"discord"`, etc. |

**Fires:** In `agent/turn_context.py` (turn preparation for `run_conversation()` in `agent/conversation_loop.py`), after context compression but before the main `while` loop. Fires once per `run_conversation()` call (i.e. once per user turn), not once per API call within the tool loop.

**Return value:** If the callback returns a dict with a `"context"` key, or a plain non-empty string, the text is appended to the current turn's user message. Return `None` for no injection.

```python
# Inject context
return {"context": "Recalled memories:\n- User likes Python\n- Working on hermes-agent"}

# Plain string (equivalent)
return "Recalled memories:\n- User likes Python"

# No injection
return None
```

**Where context is injected:** Always the **user message**, never the system prompt. This preserves the prompt cache — the system prompt stays identical across turns, so cached tokens are reused. The system prompt is Hermes's territory (model guidance, tool enforcement, personality, skills). Plugins contribute context alongside the user's input.

The clean user-message `content` remains unchanged. For replay and prompt-cache stability, Hermes may persist the exact API-bound message, including plugin-injected context, in the row's `api_content` sidecar.

On a **multimodal turn** (the user message is a list of content parts — an image attachment, or text sent as parts) there is no string sidecar: the joined context is appended to that turn's content as one extra `{"type": "text"}` part, before the first request, and the part is persisted with the turn so a resumed session, compaction and replay all see the same message the model saw. Earlier messages and the system prompt are never touched.

When **multiple plugins** return context, their outputs are joined with double newlines in plugin discovery order (alphabetical by directory name).

**Use cases:** Memory recall, RAG context injection, guardrails, per-turn analytics.

**Example — memory recall:**

```python
import httpx

MEMORY_API = "https://your-memory-api.example.com"

def recall(session_id, user_message, is_first_turn, **kwargs):
    try:
        resp = httpx.post(f"{MEMORY_API}/recall", json={
            "session_id": session_id,
            "query": user_message,
        }, timeout=3)
        memories = resp.json().get("results", [])
        if not memories:
            return None
        text = "Recalled context:\n" + "\n".join(f"- {m['text']}" for m in memories)
        return {"context": text}
    except Exception:
        return None

def register(ctx):
    ctx.register_hook("pre_llm_call", recall)
```

**Example — guardrails:**

```python
POLICY = "Never execute commands that delete files without explicit user confirmation."

def guardrails(**kwargs):
    return {"context": POLICY}

def register(ctx):
    ctx.register_hook("pre_llm_call", guardrails)
```

---

### `post_llm_call`

Fires **once per turn**, after the tool-calling loop completes and the agent has produced a final response. Only fires on **successful** turns — does not fire if the turn was interrupted.

**Callback signature:**

```python
def my_callback(session_id: str, user_message: str, assistant_response: str,
                conversation_history: list, model: str, platform: str, **kwargs):
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `session_id` | `str` | Unique identifier for the current session |
| `user_message` | `str` | The user's original message for this turn |
| `assistant_response` | `str` | The agent's final text response for this turn |
| `conversation_history` | `list` | Copy of the full message list after the turn completed |
| `model` | `str` | The model identifier |
| `platform` | `str` | Where the session is running |

**Fires:** In `agent/turn_finalizer.py` (`finalize_turn()`, called by `run_conversation()` in `agent/conversation_loop.py`), after the tool loop exits with a final response. Guarded by `if final_response and not interrupted` — so it does **not** fire when the user interrupts mid-turn or the agent hits the iteration limit without producing a response.

**Return value:** Ignored.

**Use cases:** Syncing conversation data to an external memory system, computing response quality metrics, logging turn summaries, triggering follow-up actions.

**Example — sync to external memory:**

```python
import httpx

MEMORY_API = "https://your-memory-api.example.com"

def sync_memory(session_id, user_message, assistant_response, **kwargs):
    try:
        httpx.post(f"{MEMORY_API}/store", json={
            "session_id": session_id,
            "user": user_message,
            "assistant": assistant_response,
        }, timeout=5)
    except Exception:
        pass  # best-effort

def register(ctx):
    ctx.register_hook("post_llm_call", sync_memory)
```

**Example — track response lengths:**

```python
import logging
logger = logging.getLogger(__name__)

def log_response_length(session_id, assistant_response, model, **kwargs):
    logger.info("RESPONSE session=%s model=%s chars=%d",
                session_id, model, len(assistant_response or ""))

def register(ctx):
    ctx.register_hook("post_llm_call", log_response_length)
```

---

### `pre_verify`

Fires **once per turn when the agent edited code**, just before it finishes (after the built-in verify-on-stop guard). This is a user/plugin policy gate: a callback can keep the agent going — run a check, defer it, tidy the diff — instead of letting it stop.

Hermes' shipped verification guidance is not a default `pre_verify` hook. It is appended to the evidence-based verify-on-stop nudge when edited code lacks fresh verification evidence, so it does not create a second default continuation path. Set `agent.verify_guidance: false` to keep that built-in evidence nudge terse.

**Callback signature:**

```python
def my_callback(session_id: str, platform: str, model: str, coding: bool,
                attempt: int, final_response: str, changed_paths: list, **kwargs):
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `session_id` | `str` | Unique identifier for the current session |
| `platform` | `str` | Where the session is running (`"cli"`, `"telegram"`, …) |
| `model` | `str` | The model identifier |
| `coding` | `bool` | Whether the turn is in the coding posture (in a code workspace) — scope your hook on this |
| `attempt` | `int` | How many times this turn has already been nudged (0 on the first) — self-throttle on this |
| `final_response` | `str` | The answer the agent is about to deliver |
| `changed_paths` | `list` | Files the agent edited this turn (sorted, always non-empty here) |

Scope a hook to the coding context by checking `coding` and make it one-shot with `attempt` (shell hooks read both from `.extra`), the same way a `pre_tool_call` hook scopes on `tool_name` — so you can register several `pre_verify` hooks, each firing only where it should.

**Fires:** In `agent/conversation_loop.py`, at the point the agent would accept a final answer, immediately after the verify-on-stop check — but only when the agent edited code this turn and at least one `pre_verify` hook is registered.

**Return value — keep the agent going:**

```python
return {"action": "continue", "message": "Run the formatter on your changes, then finish."}
```

The `message` is appended as a synthetic user turn and the loop runs again. The Claude-Code Stop shape (`{"decision": "block", "reason": "..."}`, where blocking the stop means *keep going*) is accepted too. A directive with no message — or any other return — lets the turn finish.

**Bounded:** consecutive continue directives in one turn are capped by `agent.max_verify_nudges` (default 3), so a hook that always says continue can never trap the loop. The attempted answer is kept in history but not surfaced to the user while the agent is being nudged.

**Make it idempotent:** the hook re-fires after each nudge, so gate on `attempt` (`if attempt: return None`) — otherwise it just nudges until the bound is hit.

**Use cases:** defer tests/lints during creative iteration, require green checks for certain paths, block "done" until a changelog entry exists, run a project-specific verification checklist.

**Example — defer checks on creative UI work, scoped + one-shot:**

```python
UI = (".tsx", ".jsx", ".css", ".scss")

def defer_ui_checks(coding, attempt, changed_paths, **kwargs):
    if attempt or not coding:
        return None  # one-shot, coding only
    if not all(p.endswith(UI) for p in changed_paths):
        return None  # only pure-UI edits
    return {
        "action": "continue",
        "message": "This is UI work — don't run tests/lints yet; ask the user to "
                   "eyeball it first, and clean the diff before any commit.",
    }

def register(ctx):
    ctx.register_hook("pre_verify", defer_ui_checks)
```

For standing guidance that should shape the built-in missing-evidence nudge, use `agent.verify_guidance`. For broader coding posture rules that don't need to *gate* verification, prefer `agent.coding_instructions` in `config.yaml` — it rides the coding brief and costs no extra turn.

---

### `transform_api_error_classification`

Fires once per failed API call, at the top of `agent/error_classifier.classify_api_error()`, before the built-in pipeline. Provider plugins use it to own their provider's error quirks without core patches. It is behavior-changing (transform family): the returned classification drives retry, compression, credential rotation, and fallback routing.

Callbacks receive the parsed error context as kwargs — `provider` (self-scope on this), `model`, `status_code`, `error_type`, `error_code`, `error_message`, `error_body`, `error`, `approx_tokens`, `context_length`, `num_messages`. Return `None` to decline, or a dict to claim the error:

```python
return {"reason": "model_not_found",   # required: a FailoverReason name
        "retryable": False, "should_fallback": True}  # optional recovery-hint overrides
```

Dispatch is run-all-then-pick-first: every callback runs, failures are isolated, and the first valid result in registration order wins (valid-but-losing results log a runtime warning). Invalid dicts and unknown reasons are skipped, so a broken plugin can never break classification.

**Privacy:** `error_message` and `error_body` may carry unredacted provider data. **Python plugins only** — shell registrations are refused at config parse with a warning.

---

### `on_session_start`

Fires **once** when a brand-new session is created. Does **not** fire on session continuation (when the user sends a second message in an existing session).

**Callback signature:**

```python
def my_callback(session_id: str, model: str, platform: str, **kwargs):
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `session_id` | `str` | Unique identifier for the new session |
| `model` | `str` | The model identifier |
| `platform` | `str` | Where the session is running |

**Fires:** In `agent/conversation_loop.py`, inside `run_conversation()`, during the first turn of a new session — specifically after the system prompt is built but before the tool loop starts. The check is `if not conversation_history` (no prior messages = new session).

**Return value:** Ignored.

**Use cases:** Initializing session-scoped state, warming caches, registering the session with an external service, logging session starts.

**Example — initialize a session cache:**

```python
_session_caches = {}

def init_session(session_id, model, platform, **kwargs):
    _session_caches[session_id] = {
        "model": model,
        "platform": platform,
        "tool_calls": 0,
        "started": __import__("datetime").datetime.now().isoformat(),
    }

def register(ctx):
    ctx.register_hook("on_session_start", init_session)
```

---

### `on_session_end`

Fires at the **very end** of every `run_conversation()` call, regardless of outcome. Also fires from the CLI's exit handler if the agent was mid-turn when the user quit.

**Callback signature:**

```python
def my_callback(session_id: str, completed: bool, interrupted: bool,
                model: str, platform: str, **kwargs):
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `session_id` | `str` | Unique identifier for the session |
| `completed` | `bool` | `True` if the agent produced a final response, `False` otherwise |
| `interrupted` | `bool` | `True` if the turn was interrupted (user sent new message, `/stop`, or quit) |
| `model` | `str` | The model identifier |
| `platform` | `str` | Where the session is running |

**Fires:** In two places:
1. **`agent/turn_finalizer.py`** — at the end of every `run_conversation()` call (`agent/conversation_loop.py`), after all cleanup. Always fires, even if the turn errored.
2. **`cli.py`** — in the CLI's atexit handler, but **only** if the agent was mid-turn (`_agent_running=True`) when the exit occurred. This catches Ctrl+C and `/exit` during processing. In this case, `completed=False` and `interrupted=True`.

**Return value:** Ignored. Despite its name this hook fires after **every turn**, so it can't show the user anything. To show something when a session ends, return a message from [`on_session_finalize`](#on_session_finalize).

**Use cases:** Flushing buffers, closing connections, persisting session state, logging session duration, cleanup of resources initialized in `on_session_start`.

**Example — flush and cleanup:**

```python
_session_caches = {}

def cleanup_session(session_id, completed, interrupted, **kwargs):
    cache = _session_caches.pop(session_id, None)
    if cache:
        # Flush accumulated data to disk or external service
        status = "completed" if completed else ("interrupted" if interrupted else "failed")
        print(f"Session {session_id} ended: {status}, {cache['tool_calls']} tool calls")

def register(ctx):
    ctx.register_hook("on_session_end", cleanup_session)
```

**Example — session duration tracking:**

```python
import time, logging
logger = logging.getLogger(__name__)

_start_times = {}

def on_start(session_id, **kwargs):
    _start_times[session_id] = time.time()

def on_end(session_id, completed, interrupted, **kwargs):
    start = _start_times.pop(session_id, None)
    if start:
        duration = time.time() - start
        logger.info("SESSION_DURATION session=%s seconds=%.1f completed=%s interrupted=%s",
                     session_id, duration, completed, interrupted)

def register(ctx):
    ctx.register_hook("on_session_start", on_start)
    ctx.register_hook("on_session_end", on_end)
```

---

### `on_session_finalize`

Fires when the CLI or gateway **tears down** an active session — for example, when the user runs `/new` or the CLI quits with an active agent. Resource-only idle cache eviction does not finalize the durable conversation. Use it to flush state tied to the outgoing session ID. On gateway reset, the replacement session already exists before this callback runs.

**Callback signature:**

```python
def my_callback(session_id: str | None, platform: str, **kwargs):
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `session_id` | `str` or `None` | The outgoing session ID. May be `None` if no active session existed. |
| `platform` | `str` | `"cli"` or the messaging platform name (`"telegram"`, `"discord"`, etc.). |

**Fires:** In CLI/TUI teardown (including the end of a `hermes -z` one-shot run, success or failure) and in gateway reset or shutdown paths. Gateway shutdown can finalize without a matching `on_session_reset`.

**Return value — show the user a message:** return a non-empty `str`, or a dict with a `"message"` string, and Hermes shows it to the user as a notice. It never becomes a model turn and never edits the assistant's reply. Return `None` to show nothing. Where it appears:

| Surface | Shown as |
|---------|---------|
| CLI `/new` | Printed in the terminal before the new session starts. |
| CLI quit | Printed above the exit summary (`hermes --resume …`). |
| `hermes chat -q` / `-z` one-shot | Printed to **stderr**, so stdout still holds only the answer. |
| TUI `/new` | A system line in the fresh session's transcript. |
| Messaging gateway `/new` or `/reset` | Sent to the chat that owned the session, as its own message. |
| Gateway shutdown / restart | Sent to each active chat before the adapters disconnect. |

It is not delivered when nobody is there to see it: idle-expiry and cache eviction in the gateway, quitting the TUI, TUI/Desktop sessions closed by the reaper or a disconnect, and Desktop new-chat or chat delete. It is also not delivered from cron jobs, which don't finalize sessions. In those cases the message is dropped and the hook still runs.

```python
def digest(session_id, **kwargs):
    count = _edits.pop(session_id, 0)
    if count:
        return {"message": f"📝 This session edited {count} file(s)."}

def register(ctx):
    ctx.register_hook("on_session_finalize", digest)
```

**Use cases:** Persist final session metrics before the session ID is discarded, close per-session resources, emit a final telemetry event, drain queued writes.

---

### `on_session_reset`

Fires at a CLI or TUI session boundary, or when the gateway **swaps in a new session key** for an active chat. This lets plugins react to cleared conversation state without waiting for the next `on_session_start`.

**Callback signature:**

```python
def my_callback(session_id: str, platform: str, **kwargs):
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `session_id` | `str` | The new session's ID (already rotated to the fresh value). |
| `platform` | `str` | `"cli"`, `"tui"`, or the messaging platform name. |
| `reason` | `str`, optional | Present on CLI and gateway reset paths. |
| `old_session_id` | `str`, optional | Gateway-only outgoing session ID. |
| `new_session_id` | `str`, optional | Gateway-only replacement session ID. |

**Fires:** CLI supplies `session_id`, `platform`, and `reason`; TUI supplies `session_id` and `platform`; gateway adds `reason`, `old_session_id`, and `new_session_id` after allocating the replacement key. On gateway reset, the order is: create and persist the replacement → `on_session_finalize(old_id)` → `on_session_reset(new_id)` → `on_session_start(new_id)` on the first inbound turn.

**Return value:** Ignored.

**Use cases:** Reset per-session caches keyed by `session_id`, emit "session rotated" analytics, prime a fresh state bucket.

---

See the **[Build a Plugin guide](../../developer-guide/plugins/index.md)** for the full walkthrough including tool schemas, handlers, and advanced hook patterns.

---

### `agent_loop_stopped`

Fires when the gateway **interrupts a running agent turn** — the user ran `/stop` while the loop was working, or the running-agent fast-path inside `/new` cleared the in-flight run before swapping the session. Unlike `on_session_finalize`, this fires earlier, while a turn is mid-flight, so plugins can drop per-turn external resources the agent loop will never consume (e.g. an outbound RPC that was waiting for a tool result).

Fires on both interruption surfaces: the messaging **gateway** (`/stop`, `/new` fast-path) and the **TUI/desktop** `session.interrupt` path (platform is reported as `"tui"`). Does not fire in the plain CLI; there is no equivalent interruption surface there.

**Callback signature:**

```python
def my_callback(session_key: str, platform: str, reason: str, invalidation_reason: str, **kwargs):
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `session_key` | `str` | The session whose run was interrupted. |
| `platform` | `str` | The messaging platform name (`"telegram"`, `"discord"`, etc.); empty string if unknown. |
| `reason` | `str` | Why the agent was interrupted (e.g. `"user_stop"`, the reset/new reason). |
| `invalidation_reason` | `str` | Why queued session state was invalidated (e.g. `"stop_command"`, `"stop_command_thread_sibling"`, `"stop_command_chat_scope"`, `"reset_command"`). |

**Fires:** In `gateway/run_agent_cache.py::_interrupt_and_clear_session`, immediately after `request_hard_interrupt()` interrupts the running agent. Only when a real agent was running — the pending-sentinel `/stop` path (no agent loop yet started) does **not** fire this hook, since there is no in-flight work to drop. On the slow `/new` reset path, `on_session_finalize` fires later in `_handle_reset_command` instead.

**Return value:** Ignored.

**Use cases:** Cancel external requests blocked on a tool result the loop will never consume, notify a connected voice/realtime client that a tool call was abandoned, release per-turn credentials or locks held only for the duration of an active turn.

---

### `subagent_start`

Fires **once per child agent** after `delegate_task` has constructed the child `AIAgent` and before that child is run. Whether you delegate a single task or a batch of three, this hook fires once for each child.

This hook is specific to delegation/subagent lifecycle. It is not a universal "before any agent invocation" gate for gateway, CLI, cron, batch, MoA, or other runner-originated agent executions.

**Callback signature:**

```python
def my_callback(parent_session_id: str | None,
                parent_turn_id: str,
                parent_subagent_id: str | None,
                child_session_id: str | None,
                child_subagent_id: str,
                child_role: str,
                child_goal: str,
                **kwargs):
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `parent_session_id` | `str \| None` | Session ID of the delegating parent agent. |
| `parent_turn_id` | `str` | Turn ID of the parent agent turn that requested delegation, if available. |
| `parent_subagent_id` | `str \| None` | Parent subagent ID when this child was spawned by another subagent; `None` for top-level parent agents. |
| `child_session_id` | `str \| None` | Session ID allocated for the child agent. |
| `child_subagent_id` | `str` | Stable subagent ID used by delegation observability and controls. |
| `child_role` | `str` | Effective child role after delegation policy is applied, for example `"leaf"` or `"orchestrator"`. |
| `child_goal` | `str` | Delegated goal/prompt that the child agent will execute. |

**Fires:** In `tools/delegate_tool.py`, inside `_build_child_agent()`, after the child `AIAgent` has been constructed and annotated with subagent identity metadata, and before `_run_single_child()` runs the child.

**Return value:** Ignored. This is an observer hook only; returning a value does not block or mutate the child agent run.

**Use cases:** Logging subagent creation, mapping parent/child session relationships, tracking nested delegation trees, emitting pre-run audit records, pre-allocating per-child observability resources.

**Example — log subagent creation:**

```python
import logging

logger = logging.getLogger(__name__)

def log_subagent_start(
    parent_session_id,
    parent_turn_id,
    child_session_id,
    child_subagent_id,
    child_role,
    child_goal,
    **kwargs,
):
    logger.info(
        "SUBAGENT_START parent=%s turn=%s child_session=%s child=%s role=%s goal=%r",
        parent_session_id,
        parent_turn_id,
        child_session_id,
        child_subagent_id,
        child_role,
        child_goal[:200],
    )

def register(ctx):
    ctx.register_hook("subagent_start", log_subagent_start)
```

:::info
`subagent_start` is useful for delegation observability, but it is not a blocking policy hook. To block delegation before a child is built, use [`pre_tool_call`](#pre_tool_call) to block the `delegate_task` tool call.
:::

---

### `subagent_stop`

Fires **once per child agent** after `delegate_task` finishes. Whether you delegated a single task or a batch of three, this hook fires once for each child. Dispatch is serialised on the parent thread after child futures drain, and each Python callback body runs on that same caller thread (not on a timeout worker).

**Callback signature:**

```python
def my_callback(parent_session_id: str, child_role: str | None,
                child_summary: str | None, child_status: str,
                tool_call_history: list[dict], duration_ms: int, **kwargs):
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `parent_session_id` | `str` | Session ID of the delegating parent agent |
| `child_role` | `str \| None` | Orchestrator role tag set on the child (`None` if the feature isn't enabled) |
| `child_summary` | `str \| None` | The final response the child returned to the parent |
| `child_status` | `str` | `"completed"`, `"failed"`, `"interrupted"`, or `"error"` |
| `tool_call_history` | `list[dict]` | Ordered metadata-only tool calls: `tool_name`, bounded `tool_input`, `input_bytes`, `output_bytes`, and `status`; raw inputs and outputs are excluded |
| `duration_ms` | `int` | Wall-clock time spent running the child, in milliseconds |

**Fires:** In `tools/delegate_tool.py`, after `ThreadPoolExecutor.as_completed()` drains all child futures. `invoke_hook("subagent_stop", ...)` is marshalled to the parent thread so authors don't see child-pool re-entrancy, and callbacks stay on that caller thread.

**Return value:** Ignored.

**Use cases:** Logging orchestration activity, accumulating child durations for billing, writing post-delegation audit records.

**Example — log orchestrator activity:**

```python
import logging
logger = logging.getLogger(__name__)

def log_subagent(parent_session_id, child_role, child_status, duration_ms, **kwargs):
    logger.info(
        "SUBAGENT parent=%s role=%s status=%s duration_ms=%d",
        parent_session_id, child_role, child_status, duration_ms,
    )

def register(ctx):
    ctx.register_hook("subagent_stop", log_subagent)
```

:::info
With heavy delegation (e.g. orchestrator roles × 5 leaves × nested depth), `subagent_stop` fires many times per turn. Keep your callback fast; push expensive work to a background queue.
:::

---

### `pre_approval_request`

Fires before an approval decision is requested. It covers prompted surfaces—interactive CLI, Ink TUI, gateway platforms, and ACP clients—including protected agent-instruction write prompts and MCP/vault consent prompts, plus `approvals.mode=smart` decisions made without a human prompt (`surface="smart"`). In smart mode, the hook runs before the auxiliary LLM is called.

This is the right place to wire a custom notifier — for example, a macOS menu-bar app that pops an allow/deny notification, or an audit log that records every approval request with context.

**Callback signature:**

```python
def my_callback(
    command: str,
    description: str,
    pattern_key: str,
    pattern_keys: list[str],
    session_key: str,
    surface: str,
    **kwargs,
):
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `command` | `str` | Terminal command or `execute_code` script being assessed. Smart and gateway payloads are redacted before observer dispatch. Smart observer redaction is mandatory even when `security.redact_secrets` is disabled; if redaction fails, smart hooks are skipped. |
| `description` | `str` | Human-readable reason(s) the command is flagged (combined when multiple patterns match) |
| `pattern_key` | `str` | Primary pattern key that triggered the approval (e.g. `"rm_rf"`, `"sudo"`) |
| `pattern_keys` | `list[str]` | All pattern keys that matched |
| `session_key` | `str` | Session identifier, useful for scoping notifications per-chat |
| `surface` | `str` | `"cli"` for classic interactive-CLI prompts (dangerous commands, protected agent-instruction writes, consent prompts), `"gateway"` for async platform approvals (the Ink TUI and Desktop app also report `"gateway"` today), `"smart"` for auxiliary-LLM auto approve/deny decisions, or the caller's consent surface for MCP/vault prompts (`"mcp-elicitation/<server>"`, `"mcp-trust/<server>"`, `"vault-payment"`) |

**Return value:** ignored. Hooks here are observer-only; they cannot veto or pre-answer the approval. Use [`pre_tool_call`](#pre_tool_call) to block a tool before it reaches the approval system.

**Use cases:** Desktop notifications, push alerts, audit logging, Slack webhooks, escalation routing, metrics.

**Example — desktop notification on macOS:**

```python
import subprocess

def notify_approval(command, description, session_key, **kwargs):
    title = "Hermes needs approval"
    body = f"{description}: {command[:80]}"
    subprocess.Popen([
        "osascript", "-e",
        f'display notification "{body}" with title "{title}"',
    ])

def register(ctx):
    ctx.register_hook("pre_approval_request", notify_approval)
```

---

### `post_approval_response`

Fires after a prompted or smart approval decision, after a prompt times out or is withdrawn (turn interrupted or ended before an answer), or when the gateway cannot deliver the approval notification. Notification failure emits `choice="notify_failed"` before any approval decision exists.

**Callback signature:**

```python
def my_callback(
    command: str,
    description: str,
    pattern_key: str,
    pattern_keys: list[str],
    session_key: str,
    surface: str,
    choice: str,
    **kwargs,
):
```

Same kwargs as `pre_approval_request`, plus:

| Parameter | Type | Description |
|-----------|------|-------------|
| `choice` | `str` | Prompted surfaces use `"once"`, `"session"`, `"always"`, `"deny"`, `"timeout"`, `"cancelled"` (nobody answered — the prompt was withdrawn because the turn was interrupted or ended, or on the CLI it never reached the user because the approval callback failed, no callback was registered under prompt_toolkit, or the read was interrupted; the command did not run), or `"notify_failed"`; smart decisions use `"smart_approve"` or `"smart_deny"` |
| `decided_by` | `str` | `"aux_llm"` for smart decisions; absent on prompted surfaces |

**Return value:** ignored.

**Use cases:** Close the matching desktop notification, record the final decision in an audit log, update metrics, roll forward a rate limiter.

```python
def log_decision(command, choice, session_key, **kwargs):
    logger.info("approval %s: %s for session %s", choice, command[:60], session_key)

def register(ctx):
    ctx.register_hook("post_approval_response", log_decision)
```

---

### `on_human_input_request`

Fires right before Hermes blocks waiting for a person to type or click something, whatever the prompt is and wherever it shows up. One hook pair covers every blocking prompt, so a notifier ("Hermes is waiting on you") does not have to subscribe to a separate hook per prompt type or pattern-match tool arguments:

| `kind` | Prompt | Surfaces |
|--------|--------|----------|
| `"sudo"` | Masked sudo password prompt before a `sudo` command runs | Interactive CLI, Ink TUI / Desktop (`sudo.request` card), `/dev/tty` fallback |
| `"clarify"` | A `clarify` tool question | CLI, Ink TUI / Desktop, gateway platforms |
| `"approval"` | Dangerous-command / write approval, MCP elicitation consent, plugin approval transports | CLI, Ink TUI / Desktop, ACP, gateway platforms, `ctx.register_approval_transport` plugins |

`approvals.mode=smart` decisions made by the auxiliary LLM do not fire it, because no person is asked; neither do gateway approvals that join an identical prompt already pending (the person sees one prompt, and the pair fires once for it). New kinds can be added later, so ignore kinds you don't handle.

**Callback signature:**

```python
def my_callback(kind: str, request_id: str, session_id: str, session_key: str,
                platform: str, prompt: str, **kwargs):
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `kind` | `str` | `"sudo"`, `"clarify"` or `"approval"` |
| `request_id` | `str` | Unique per prompt; the matching `on_human_input_resolved` carries the same value |
| `session_id` | `str` | Hermes session id when known, else `""` |
| `session_key` | `str` | Session/chat routing key, useful for scoping notifications per chat |
| `platform` | `str` | Gateway platform (`"telegram"`, `"discord"`, ...) or local source (`"cli"`, `"tui"`, `"desktop"`, ...) |
| `prompt` | `str` | What the person is asked about: the command for `sudo`/`approval`, the question text for `clarify`. Always redacted with the secret redactor, even when `security.redact_secrets` is off |

The sudo password and clarify answers are **never** passed to either hook. **Return value:** ignored; a raising callback is logged and the prompt proceeds. Callbacks run on the thread that is about to block, so keep them fast — hand network calls (push services, webhooks) to a background thread.

### `on_human_input_resolved`

Fires exactly once for every `on_human_input_request`, after the wait ends. Same kwargs plus `outcome`:

| `kind` | `outcome` values |
|--------|------------------|
| `"sudo"` | `"provided"`, `"skipped"` (empty answer), `"timeout"`, `"cancelled"`, `"error"` |
| `"clarify"` | The surface's outcome: `"submitted"`, `"timed_out"`, `"cancelled"`, `"undelivered"`, `"terminated"`, or `"error"` |
| `"approval"` | `"once"`, `"session"`, `"always"`, `"deny"`, `"timeout"`, `"cancelled"`, `"notify_failed"`, or `"transport_<failure>"` for a failed plugin transport |

```python
import threading

def push_notification(text):
    ...  # call your push service here

def register(ctx):
    pending = {}

    def on_request(kind, request_id, prompt, session_key, **kwargs):
        pending[request_id] = threading.Timer(30, push_notification, (f"Hermes needs you ({kind}): {prompt[:80]}",))
        pending[request_id].start()  # only page if nobody answers within 30s

    def on_resolved(request_id, **kwargs):
        timer = pending.pop(request_id, None)
        if timer:
            timer.cancel()

    ctx.register_hook("on_human_input_request", on_request)
    ctx.register_hook("on_human_input_resolved", on_resolved)
```

---

### `pre_transcription`

Fires inside the STT dispatcher (`tools.transcription_tools.transcribe_audio`) **after** the provider has been resolved and **before** any backend is invoked, whether that backend is built-in, a `type: command` provider, or a plugin-registered provider. Lets a plugin steer the transcription request itself instead of only observing the transcript afterwards.

**Callback signature:**

```python
def my_callback(
    file_path: str,
    provider: str,
    model: str | None,
    language: str | None,
    prompt: str | None,
    source: str | None,
    **kwargs,
) -> dict | None:
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `file_path` | `str` | Absolute path to the audio file about to be transcribed. Read-only. |
| `provider` | `str` | Resolved STT provider (`local`, `groq`, `openai`, `mistral`, `xai`, `elevenlabs`, `deepinfra`, `local_command`, a command provider name, or a plugin provider name). |
| `model` | `str \| None` | Model resolved so far, or `None` when the backend default applies. |
| `language` | `str \| None` | Language from the provider's config section, or `None`. |
| `prompt` | `str \| None` | The static [`stt.prompt`](../configuration.md#transcription-prompt-vocabulary-hints) value, or `None`. |
| `source` | `str \| None` | Caller surface label (`gateway`, `voice_mode`, …). Observability only, not used for dispatch. |

**Return value:** a `dict` with any of `"prompt"`, `"language"`, `"model"` mapped to strings, or `None` to leave the request unchanged. Non-string values, unknown keys, and `file_path` are ignored (`file_path` attempts are logged as a warning). Results are applied in **registration order, last-writer-wins per field**, on top of the `stt.prompt` config value. Returning `""` for `prompt` clears the configured prompt for that request.

**Use cases:** Inject a per-user or per-chat vocabulary list before the audio is uploaded, force `language` from the caller's locale, downgrade `model` for long recordings, route noisy sources to a different model.

```python
VOCAB = "Hermes, Teknium, Nous Research, kanban"

def add_vocab(provider, prompt, source, **kwargs):
    if source != "gateway":
        return None
    return {"prompt": f"{prompt}. {VOCAB}" if prompt else VOCAB}

def register(ctx):
    ctx.register_hook("pre_transcription", add_vocab)
```

Not every backend accepts a prompt. `local` maps it to faster-whisper's `initial_prompt`; `openai`, `groq`, `mistral`, and `deepinfra` send it as `prompt`; `xai`, `elevenlabs`, `local_command`, and `type: command` providers log at DEBUG and transcribe without it. See the [provider support table](../configuration.md#transcription-prompt-vocabulary-hints) for the full matrix and the privacy boundary. Hook-plumbing errors are fail-open: the dispatch continues with the unmodified request.

---

### `transform_tool_result`

Fires **after** a tool returns and **before** the result is appended to the conversation. Lets a plugin rewrite ANY tool's result string — not just terminal output — before the model sees it.

**Callback signature:**

```python
def my_callback(tool_name: str, args: dict, result: str, task_id: str, **kwargs) -> str | None:
```

The full payload also includes `session_id`, `tool_call_id`, `turn_id`, `api_request_id`, `duration_ms`, `status`, `error_type`, and `error_message`. `result` is the final result returned by tool dispatch; it and `args` can contain arbitrary user/tool content and secrets.

**Return value:** The first `str` replaces the result (including an empty string); `None` leaves it unchanged.

**Use cases:** Redact organization-specific PII from `web_extract` output, wrap long JSON tool responses in a summary header, inject retrieval-augmented hints into `read_file` results, rewrite `delegate_task` subagent reports into a project-specific schema.

```python
import re
SECRET = re.compile(r"sk-[A-Za-z0-9]{32,}")

def redact_secrets(tool_name, result, **kwargs):
    if SECRET.search(result):
        return SECRET.sub("[REDACTED]", result)
    return None

def register(ctx):
    ctx.register_hook("transform_tool_result", redact_secrets)
```

Applies to every tool. For terminal-only rewriting see `transform_terminal_output` below — it is narrower, runs before `transform_tool_result`, and its replacement is still subject to the terminal tool's final output limit.

---

### `transform_terminal_output`

Fires inside the `terminal` tool after foreground process capture has already been bounded by the environment, and before the final output limit. It lets plugins replace the captured stdout/stderr; the replacement is still subject to the final output limit.

It also fires for background-process output on its way to the model or the chat: the `process_manage` `poll` / `wait` / `log` / `kill` results (and `list` previews) and the completion, heartbeat and watch-pattern notifications. There `returncode` is `None` while the process is still running and `env_type` is an empty string (the environment is not recorded per process). In both cases the hook runs *before* secret redaction, so a replacement that still carries a credential is masked.

**Callback signature:**

```python
def my_callback(
    command: str,
    output: str,
    returncode: int,
    task_id: str,
    env_type: str,
    **kwargs,
) -> str | None:
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `command` | `str` | The shell command that produced the output. |
| `output` | `str` | Combined stdout/stderr after bounded process capture. |
| `returncode` | `int` | Process return code. |
| `task_id` | `str` | Effective task identifier, or an empty string. |
| `env_type` | `str` | Execution-environment type. |

**Return value:** First `str` replaces the output; `None` leaves it unchanged. Command and output can contain credentials or other sensitive data.

```python
def summarize_find(command, output, **kwargs):
    if command.startswith("find ") and len(output) > 50_000:
        lines = output.count("\n")
        head = "\n".join(output.splitlines()[:40])
        return f"{head}\n\n[summary: {lines} paths total, showing first 40]"
    return None

def register(ctx):
    ctx.register_hook("transform_terminal_output", summarize_find)
```

Pairs with `transform_tool_result`, which runs afterward for every tool, including `terminal`.

---

### `transform_llm_output`

Fires **once per turn** after the tool-calling loop completes and the model has produced a final response, **before** that response is delivered to the user (CLI, gateway, or programmatic caller) and **before** the assistant row is persisted — the replacement is what the session stores, what `/resume` shows and what the next turn replays, so the transcript never diverges from what the user saw. Hermes' own trailers (the file-mutation warning, the abnormal-exit note) are appended afterwards and are not part of `response_text`. Lets a plugin rewrite the assistant's final text using classical-programming methods — no extra inference tokens burned on SOUL flavor text or a skill-driven transform.

**Callback signature:**

```python
def my_callback(
    response_text: str,
    session_id: str,
    model: str,
    platform: str,
    **kwargs,
) -> str | None:
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `response_text` | `str` | The assistant's final response text for this turn. |
| `session_id` | `str` | Session ID for this conversation (may be empty for one-shot runs). |
| `model` | `str` | Model name that produced the response (e.g. `anthropic/claude-sonnet-4.6`). |
| `platform` | `str` | Delivery platform (`cli`, `telegram`, `discord`, …; empty when unset). |

**Return value:** Non-empty `str` to replace the response text, `None` or empty string to leave it unchanged. **First non-empty string wins** when multiple plugins register. Unlike the tool and terminal transforms, an empty string is not accepted as a replacement.

**Use cases:** Apply a personality/vocabulary transform (pirate-speak, Spongebob), redact user-specific identifiers from the final text, append a project-specific signature footer, enforce a house style guide without burning tokens on SOUL instructions.

When CLI streaming is enabled, an append-only transform is printed after the
streamed body. A transform that replaces the response is printed in full after
the streamed body, labeled as a post-stream transformation, so replacement
content is never silently lost.

```python
import os, re

def spongebob(response_text, **kwargs):
    if os.environ.get("SPONGEBOB_MODE") != "on":
        return None  # pass through unchanged
    return re.sub(r"!", "!! Tartar sauce!", response_text)

def register(ctx):
    ctx.register_hook("transform_llm_output", spongebob)
```

The hook is guarded on a non-empty, non-interrupted response — it will not fire on stop-button interrupts or empty turns. Exceptions are logged as warnings and do not break agent execution.

### API-request observer hooks

#### `pre_api_request`

Fires for each provider attempt immediately before sending it. This is observer-only. The legacy `user_message`, `conversation_history`, and `request_messages` fields are raw and intentionally unsanitized for compatibility; new consumers should prefer the sanitized `request` envelope.

#### `post_api_request`

Fires after a provider response has been normalized successfully. This is observer-only. Prefer the sanitized `response`; `assistant_message` is the raw normalized message, and `usage` contains accounting data.

#### `api_request_error`

Fires for a failed provider attempt with status/retry timing, an `error` object, and sanitized `request`. This is observer-only. Error messages may still contain provider or user data.

### Auxiliary-call observer hooks

#### `pre_auxiliary_call` / `post_auxiliary_call`

Auxiliary LLM calls — session titling, context compression, MoA advisors and the aggregator, vision, approval classification, memory and other side tasks — run outside the main tool-calling loop and do **not** fire `pre_api_request` / `post_api_request` (those stay turn-scoped, so a trace-per-turn plugin never sees side traffic by accident). Subscribe to `pre_auxiliary_call` / `post_auxiliary_call` instead: they fire once per physical provider attempt (retries and fallbacks included) with the same payload shape plus `aux_task` (the task name, e.g. `title_generation`, `compression`, `moa_aggregator`, `vision`). `session_id` / `task_id` / `turn_id` are the parent turn's when the call runs under one, empty otherwise; `api_request_id` (`aux-…`) is shared by every attempt of one logical call and `retry_count` distinguishes them. Both are observer-only and fail-open: a raising or timed-out callback is logged and the auxiliary task proceeds. `post_auxiliary_call` carries `error` / `error_type` when the attempt raised and `streaming: True` (with `usage`/`response` `None`) when the response is handed back as a stream.

### `on_skill_lifecycle`

Fires after an authoritative skill-usage state change. It is observer-only and exposes the local `skill_name`, provenance, correlation IDs, usage count, and reuse flags.

### Kanban lifecycle observers

#### `kanban_task_claimed`

Fires after the claim commit in the dispatcher process, immediately before worker spawn.

#### `kanban_task_completed`

Fires after completion and cleanup, usually in the worker process. Its `summary` can contain project or user content.

#### `kanban_task_blocked`

Fires after a normal blocked transition. The dependency-wait path invokes it before that write transaction exits. Its `reason` can contain project or user content.

All three kanban hooks are observer-only and carry `task_id`, `profile_name`, `board`, `assignee`, and `run_id`; completed adds `summary`, and blocked adds `reason`.

### Kanban worker-lifecycle, task-mutation, and dispatch observers

Five additional observers (RFC #58548) extend the kanban family. All are observer-only, fire after the relevant transaction commits, and short-circuit on `has_hook` — with no subscriber, dispatch behavior is unchanged. Task-scoped hooks carry the same common fields as the hooks above.

- **`on_kanban_worker_spawned`** — after `spawn_fn` returns and the worker PID is persisted. Adds `worker_pid` (may be `None`) and `workspace_path`. Runs inside the dispatch lock; keep callbacks fast.
- **`on_kanban_worker_exited`** — tick-derived, when `detect_crashed_workers` reclaims a dead-PID task. Adds `worker_pid`, `exit_kind`, `exit_code`, `outcome`, `retry_status`.
- **`on_kanban_worker_stale_claim`** — when a TTL-expired claim is reclaimed; live-PID extensions don't fire. Adds `worker_pid`, `heartbeat_stale`, `retry_status`.
- **`on_kanban_task_updated`** — after a committed task-field write outside the claim/complete/block lifecycle (`assign_task`, model/reasoning overrides, dashboard editors). Adds `changed_fields` — field names only, never values.
- **`on_kanban_dispatch_tick`** — once per dispatcher tick, strictly after the dispatch lock is released, including idle and lock-contended ticks. Payload: `board`, `profile_name`, `dry_run`, `outcome`, `result`.

---

## Shell Hooks

Declare shell-script hooks in your profile's `config.yaml` and Hermes will run them as subprocesses whenever the corresponding plugin-hook event fires — in CLI, gateway, Desktop, TUI, and dashboard chat sessions. No Python plugin authoring required.

Desktop, TUI, and dashboard chat register hooks when building an agent, using that session's profile configuration and consent allowlist. Switching profiles does not reuse another profile's hooks. Existing hook consent requirements and safe-mode behavior still apply; unapproved hooks are skipped rather than silently approved.

Use shell hooks when you want a drop-in, single-file script (Bash, Python, anything with a shebang) to:

- **Block or modify a tool call** — reject dangerous `terminal` commands, enforce per-directory policies, require approval for destructive `write_file` / `patch` operations, or rewrite arguments (sanitize paths, inject defaults) before the tool runs.
- **Run after a tool call** — auto-format Python or TypeScript files that the agent just wrote, log API calls, trigger a CI workflow.
- **Inject context into the next LLM turn** — prepend `git status` output, the current weekday, or retrieved documents to the user message (see [`pre_llm_call`](#pre_llm_call)).
- **Observe lifecycle events** — write a log line when a subagent completes (`subagent_stop`) or a session starts (`on_session_start`).

Shell hooks are registered by calling `agent.shell_hooks.register_from_config(cfg)` at both CLI startup (`hermes_cli/main.py`) and gateway startup (`gateway/run.py`). They compose naturally with Python plugin hooks — both flow through the same dispatcher.

### Comparison at a glance

|-----------|-------------|-------------------------------|---------------------------------------|
| Declared in | `hooks:` block in `~/.hermes/config.yaml` | `register()` in a `plugin.yaml` plugin | `HOOK.yaml` + `handler.py` directory |
| Lives under | `~/.hermes/agent-hooks/` (by convention) | `~/.hermes/plugins/<name>/` | `~/.hermes/hooks/<name>/` |
| Language | Any (Bash, Python, Go binary, …) | Python only | Python only |
| Runs in | CLI + TUI | CLI + TUI | Gateway only |
| Events | `VALID_HOOKS` (incl. `subagent_stop`) | `VALID_HOOKS` | Gateway lifecycle (`gateway:startup`, `agent:*`, `command:*`) |
| Can block a tool call | Yes (`pre_tool_call`) | Yes (`pre_tool_call`) | No |
| Can inject LLM context | Yes (`pre_llm_call`) | Yes (`pre_llm_call`) | No |
| Consent | First-use prompt per `(event, command)` pair | Explicit (`plugins.enabled`), then in-process trust | Implicit ([dir trust](#gateway-hook-trust)) |
| Inter-process isolation | Yes (subprocess) | No (in-process) | No (in-process) |

### Configuration schema

```yaml
hooks:
  <event_name>:                  # Must be in VALID_HOOKS
    - matcher: "<regex>"         # Optional; used for pre/post_tool_call only
      command: "<shell command>" # Required; runs via shlex.split, shell=False
      timeout: <seconds>         # Optional; default 60, capped at 300
      fail_closed: <bool>        # Optional; default false. pre_tool_call only.
                                 # `failClosed` also accepted (Cursor/Claude Code compat)

hooks_auto_accept: false         # See "Consent model" below
```

Event names must be one of the [plugin hook events](#plugin-hooks); typos produce a "Did you mean X?" warning and are skipped. Unknown keys inside a single entry are ignored; missing `command` is a skip-with-warning. `timeout > 300` is clamped with a warning. `fail_closed: true` on an event other than `pre_tool_call` warns and is ignored (only blocking-capable events can fail closed).

On Windows, a `command` that starts with an existing script file — the `~/.hermes/agent-hooks/x.sh` shape the examples below use — is spawned through that file's own interpreter (Git Bash for `.sh`/`.bash`, the running Hermes Python for `.py`), because `CreateProcess` has no shebang support and rejects a bare script with `WinError 193`. Every other command, and every POSIX platform, passes `argv` straight to `Popen`, where the kernel already honours the shebang.

### JSON wire protocol

Each time the event fires, Hermes spawns a subprocess for every matching hook (matcher permitting), pipes a JSON payload to **stdin**, and reads **stdout** back as JSON.

**stdin — payload the script receives:**

```json
{
  "hook_event_name": "pre_tool_call",
  "tool_name":       "terminal",
  "tool_input":      {"command": "rm -rf /"},
  "session_id":      "sess_abc123",
  "cwd":             "/home/user/project",
  "profile":         "default",
  "extra":           {"task_id": "...", "tool_call_id": "..."}
}
```

`profile` names the Hermes profile that fired the hook (`"default"` outside profiles), so one
script can serve every profile behind a multiplexed gateway; the subprocess also runs with that
profile's `HERMES_HOME`. `tool_name` and `tool_input` are `null` for non-tool events (`pre_llm_call`, `subagent_stop`, session lifecycle). The `extra` dict carries all event-specific kwargs (`user_message`, `conversation_history`, `child_role`, `duration_ms`, …). Unserialisable values are stringified rather than omitted.

**stdout — optional response:**

```jsonc
// Block a pre_tool_call (both shapes accepted; normalised internally):
{"decision": "block", "reason":  "Forbidden: rm -rf"}   // Claude-Code style
{"action":   "block", "message": "Forbidden: rm -rf"}   // Hermes-canonical

// Modify a pre_tool_call — rewrite tool args before dispatch:
{"action": "modify", "args": {"new_string": "fixed content"}}         // Hermes-canonical
{"decision": "modify", "tool_input": {"new_string": "fixed content"}} // Claude-Code style

// Escalate a pre_tool_call to the human-approval gate (Hermes-only; `message` and `rule_key`
// are optional). Claude-Code's `{"decision": "approve"}` means auto-allow and is NOT mapped here:
{"action": "approve", "message": "Why approval is required", "rule_key": "optional:scope"}

// Inject context for pre_llm_call:
{"context": "Today is Friday, 2026-04-17"}

// Keep the agent going at the verify gate (pre_verify); both shapes accepted:
{"action": "continue", "message": "Run the formatter, then finish."}
{"decision": "block",  "reason":  "Run the formatter, then finish."}

// Silent no-op — any empty / non-matching output is fine:
```

Except for `pre_tool_call` exit code 2 described below, malformed JSON, ordinary non-zero exit codes, and timeouts fail open by default: they log a warning but do not abort the agent loop. A `fail_closed` hook changes the behavior as described below.

### Exit code 2 = block (Claude Code / Cursor compatible)

A `pre_tool_call` hook that exits with code **2** blocks the tool call even when its stdout carries no block JSON. The block message is resolved in priority order:

1. stdout block JSON (`reason` / `message`), when present;
2. the first 400 characters of stderr;
3. a generic `"Blocked by shell hook."` default.

So the simplest possible blocking hook is:

```bash
#!/usr/bin/env bash
echo "policy violation: rm -rf is not permitted" >&2
exit 2
```

For events whose block directive is not honored (everything except `pre_tool_call`), exit 2 is treated like any other non-zero exit: a warning is logged and stdout is still parsed.

### Fail-open vs fail-closed

By default shell hooks **fail open**: a spawn error, timeout, or unparseable stdout logs a warning and the action proceeds. That is the right default for observability hooks — but wrong for security gates. A crashed secret-scanner must not silently allow the tool call it was supposed to vet.

Set `fail_closed: true` (or `failClosed: true`, the Cursor/Claude Code spelling) on a `pre_tool_call` entry to invert that:

```yaml
hooks:
  pre_tool_call:
    - matcher: "terminal|write_file|patch"
      command: "~/.hermes/agent-hooks/secret-scan.sh"
      timeout: 10
      fail_closed: true
```

With `fail_closed: true`, each of these now **blocks** the tool call with `hook <command> failed closed: <reason>`:

| Failure | Fail-open (default) | `fail_closed: true` |
|---------|--------------------|--------------------|
| Command not found / not executable | warn, proceed | **block** |
| Timeout | warn, proceed | **block** |
| Non-zero exit with no recognized directive | warn, proceed | **block** |
| Non-JSON stdout (e.g. a stack trace) | warn, proceed | **block** |
| Clean exit, valid no-op JSON (`{}`) | proceed | proceed |

`fail_closed` only applies to blocking-capable events (`pre_tool_call` today); setting it on any other event logs a warning at config-parse time and is ignored. `hermes hooks test` reflects these semantics — the `parsed` line shows exactly the block shape the dispatcher would receive.

### Worked examples

#### 1. Auto-format Python files after every write

```yaml
# ~/.hermes/config.yaml
hooks:
  post_tool_call:
    - matcher: "write_file|patch"
      command: "~/.hermes/agent-hooks/auto-format.sh"
```

```bash
#!/usr/bin/env bash
# ~/.hermes/agent-hooks/auto-format.sh
payload="$(cat -)"
path=$(echo "$payload" | jq -r '.tool_input.path // empty')
[[ "$path" == *.py ]] && command -v black >/dev/null && black "$path" 2>/dev/null
printf '{}\n'
```

The agent's in-context view of the file is **not** re-read automatically — the reformat only affects the file on disk. Subsequent `read_file` calls pick up the formatted version.

#### 2. Block destructive `terminal` commands

```yaml
hooks:
  pre_tool_call:
    - matcher: "terminal"
      command: "~/.hermes/agent-hooks/block-rm-rf.sh"
      timeout: 5
```

```bash
#!/usr/bin/env bash
# ~/.hermes/agent-hooks/block-rm-rf.sh
payload="$(cat -)"
cmd=$(echo "$payload" | jq -r '.tool_input.command // empty')
if echo "$cmd" | grep -qE 'rm[[:space:]]+-rf?[[:space:]]+/'; then
  printf '{"decision": "block", "reason": "blocked: rm -rf / is not permitted"}\n'
else
  printf '{}\n'
fi
```

#### 3. Inject `git status` into every turn (Claude-Code `UserPromptSubmit` equivalent)

```yaml
hooks:
  pre_llm_call:
    - command: "~/.hermes/agent-hooks/inject-cwd-context.sh"
```

```bash
#!/usr/bin/env bash
# ~/.hermes/agent-hooks/inject-cwd-context.sh
cat - >/dev/null   # discard stdin payload
if status=$(git status --porcelain 2>/dev/null) && [[ -n "$status" ]]; then
  jq --null-input --arg s "$status" \
     '{context: ("Uncommitted changes in cwd:\n" + $s)}'
else
  printf '{}\n'
fi
```

Claude Code's `UserPromptSubmit` event is intentionally not a separate Hermes event — `pre_llm_call` fires at the same place and already supports context injection. Use it here.

#### 4. Log every subagent completion

```yaml
hooks:
  subagent_stop:
    - command: "~/.hermes/agent-hooks/log-orchestration.sh"
```

```bash
#!/usr/bin/env bash
# ~/.hermes/agent-hooks/log-orchestration.sh
log=~/.hermes/logs/orchestration.log
jq -c '{ts: now, parent: .session_id, extra: .extra}' < /dev/stdin >> "$log"
printf '{}\n'
```

### Consent model

Each unique `(event, command)` pair prompts the user for approval the first time Hermes sees it, then persists the decision to `~/.hermes/shell-hooks-allowlist.json`. Subsequent runs (CLI or gateway) skip the prompt.

Three escape hatches bypass the interactive prompt — any one is sufficient:

1. `--accept-hooks` flag on the CLI (e.g. `hermes --accept-hooks chat`)
2. `HERMES_ACCEPT_HOOKS=1` environment variable
3. `hooks_auto_accept: true` in `~/.hermes/config.yaml`

Non-TTY runs (gateway, cron, CI) need one of these three — otherwise any newly-added hook silently stays un-registered and logs a warning.

**Script edits are silently trusted.** The allowlist keys on the exact command string, not the script's hash, so editing the script on disk does not invalidate consent. `hermes hooks doctor` flags mtime drift so you can spot edits and decide whether to re-approve.

#### Manual allowlisting

Manual allowlisting is useful for non-TTY or service-account deployments where an operator cannot answer the first-use prompt interactively. The allowlist file is `~/.hermes/shell-hooks-allowlist.json`, and the expected format is an `approvals` array. Each approval records the hook `event` and the exact `command` string:

```json
{
  "approvals": [
    {
      "event": "post_llm_call",
      "command": "/home/hermes/.hermes/hooks/my-hook.py"
    }
  ]
}
```

The command string must match the configured hook command exactly. A path-keyed object with a `sha256` field is not the expected format and will not approve the hook. Verify manual entries with `hermes hooks list`.

### The `hermes hooks` CLI

| Command | What it does |
|---------|--------------|
| `hermes hooks list` | Dump configured hooks with matcher, timeout, and consent status |
| `hermes hooks test <event> [--for-tool X] [--payload-file F]` | Fire every matching hook against a synthetic payload and print the parsed response |
| `hermes hooks revoke <command>` | Remove every allowlist entry matching `<command>` (takes effect on next restart) |
| `hermes hooks doctor` | For every configured hook: check exec bit, allowlist status, mtime drift, JSON output validity, and rough execution time |

### Security

Shell hooks run with **your full user credentials** — same trust boundary as a cron entry or a shell alias. Treat the `hooks:` block in `config.yaml` as privileged configuration:

- Only reference scripts you wrote or fully reviewed.
- Keep scripts inside `~/.hermes/agent-hooks/` so the path is easy to audit.
- Re-run `hermes hooks doctor` after you pull a shared config to spot newly-added hooks before they register.
- If your config.yaml is version-controlled across a team, review PRs that change the `hooks:` section the same way you'd review CI config.

### Ordering and precedence

Both Python plugin hooks and shell hooks flow through the same `invoke_hook()` dispatcher. Python plugins are registered first (`discover_and_load()`), shell hooks second (`register_from_config()`), so Python `pre_tool_call` decisions take precedence in tie cases. The first valid block wins — the aggregator returns as soon as any callback produces `{"action": "block", "message": str}` with a non-empty message — and a block anywhere in the list outranks an `approve` returned earlier.

## Outbound Webhooks

Outbound webhooks notify explicitly configured external endpoints about local lifecycle events. Configure a list of HTTP endpoints and the lifecycle events they care about, and Hermes POSTs a signed JSON payload to each endpoint whenever a matching event fires — no polling on the receiving end.

Typical uses:

- Notify a CI system or dashboard when an agent turn finishes (`on_session_end`)
- Track subagent completions across a fleet (`subagent_stop`)
- Feed tool activity into external monitoring (`post_tool_call` with a `matcher`)
- Wake *another* Hermes instance: point the URL at that instance's inbound webhook

### Configuration

Add a `hooks.outbound:` list to `~/.hermes/config.yaml`:

```yaml
hooks:
  outbound:
    - name: ci-notify                       # optional label for logs
      url: https://ci.example.com/hermes-events
      events: [on_session_end, subagent_stop]
      secret_env: HERMES_OUTBOUND_WEBHOOK_SECRET   # env var holding the HMAC secret
      timeout: 10                           # per-attempt seconds (1–60)

    - name: tool-monitor
      url: https://metrics.example.com/hooks/hermes
      events: [post_tool_call]
      matcher: "terminal|delegate_task"     # regex, tool-scoped events only
```

Any event from the plugin-hook set is valid (`pre_tool_call`, `post_tool_call`, `pre_llm_call`, `post_llm_call`, `on_session_start`, `on_session_end`, `subagent_start`, `subagent_stop`, ...). Malformed entries warn and are skipped — a broken webhook never crashes the agent. Changes take effect on the next CLI session / gateway restart.

Secrets: prefer `secret_env` (the name of an environment variable, typically set in `~/.hermes/.env`) over an inline `secret:` literal, so the config file stays free of credentials. Entries without a secret are delivered unsigned (flagged as `UNSIGNED` by `hermes hooks list`).

### Wire format

Each firing POSTs a JSON body with the same top-level shape as shell hooks' stdin, plus delivery metadata. `profile` names the Hermes profile that emitted the event (`"default"` outside profiles), so receivers behind a multiplexed gateway can tell profiles apart:

```json
{
  "hook_event_name": "on_session_end",
  "profile": "default",
  "tool_name": null,
  "tool_input": null,
  "session_id": "sess_abc123",
  "cwd": "/home/user/project",
  "extra": {"completed": true, "interrupted": false, "model": "...", "platform": "cli"},
  "delivery_id": "3f2c9a...",
  "timestamp": "2026-07-22T14:00:00Z"
}
```

Headers:

| Header | Value |
|--------|-------|
| `Content-Type` | `application/json` |
| `X-Hermes-Event` | The hook event name |
| `X-Hermes-Delivery` | Unique id per delivery — same value as `delivery_id` in the body |
| `X-Hermes-Signature-256` | `sha256=<hex>` — HMAC-SHA256 of the raw body, GitHub-style; only present when a secret is configured |

Verify the signature exactly as you would a GitHub webhook:

```python
import hashlib, hmac

def verify(body: bytes, header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)
```

Because `delivery_id` and `timestamp` live **inside the signed body**, a verified receiver also gets replay protection for free:

- **Dedupe** on `delivery_id` (or the matching `X-Hermes-Delivery` header) — remember recently seen ids and skip duplicates. Hermes retries failed deliveries once, so the same id can legitimately arrive twice.
- **Reject stale events** by checking `timestamp` against your clock with a tolerance window (5 minutes is the common default). An attacker replaying a captured request can't forge a fresh timestamp without the secret.

### Delivery semantics

- **Fire-and-forget, off the hot path.** Events are serialized and queued instantly; a single background thread performs the HTTP POSTs. A slow or dead endpoint can never stall a tool call or an agent turn.
- **Notify-only.** Unlike shell hooks, outbound webhooks cannot block tool calls or inject context — the response body is ignored. They observe, never steer.
- **Bounded retries.** Connection errors and 5xx responses are retried once with backoff; 4xx responses are not retried (the receiver said the request itself is wrong). Failures are logged and dropped — delivery is best-effort, not guaranteed.
- **Redirects are never followed.** A 3xx response is treated as a misconfiguration and logged — following a redirected POST would silently drop the signed payload. Point the `url` at the final endpoint.
- **Bounded queue.** If the queue backs up (dead endpoint, event storm), new events are dropped with a warning rather than consuming unbounded memory.
- **No consent prompt.** Outbound targets execute no code on your machine — they receive data at a URL you configured. `HERMES_SAFE_MODE=1` still skips registration, same as plugins and shell hooks. Note that payloads include tool inputs and event metadata, so only point targets at endpoints you trust, and prefer `https://`.

`hermes hooks list` shows configured outbound targets alongside shell hooks, including whether each target is signed.
