---
title: Local Model Sidecars
description: Optional task-aware tool review and cache-aware routing through your model pool.
sidebar_label: Local Model Sidecars
---

# Local Model Sidecars

Hermes can run two small local models alongside your main agent. Enable each independently:

| Mechanism | Input | Result |
| --- | --- | --- |
| Tool guard | Authorized task, workspace, actual tool contract and arguments, recent history | Approve, ask the operator, or deny without asking |
| Model router | Task scope, recent progress/failures, current context size and your model pool | Stay on the current route or switch to an eligible model |

Both are disabled by default. Their configuration is captured when a new agent session starts;
changing settings does not rebuild an existing conversation's prompt or tools. Downloads and
the managed llama.cpp runtime are shared on the machine; policies, credentials, routing pools,
cache observations and routing history belong to the active profile/session.

## Install and check

```bash
hermes model sidecars list
hermes model sidecars setup --role both --source huggingface
hermes model sidecars check
hermes model sidecars enable --role guard
```

Use `--source modelscope` to download the same pinned GGUF artifacts from China's
[ModelScope hub](https://modelscope.cn/models/unsloth/Qwen3.5-4B-GGUF). Downloads are verified
against exact sizes and SHA-256 digests before atomic publication. Only model data is loaded;
repository Python code is not executed. Existing managed CPU/GPU runtime installation and
hardware-aware launch planning handle inference. `--backend cpu` forces CPU inference.

The default guard is **Qwen3.5-4B Q4_K_M** and the router is **Qwen3.5-2B Q4_K_M**.
Together they download approximately 4.02 GB (3.75 GiB), plus the runtime. Context ceilings
are 8,192 tokens for guard aliases and 24,576 for router aliases; hardware limits may lower
them. These aliases preserve the larger context of a main agent using the same model weights.
The initial decision budgets are 12,000 characters for the guard and 48,000 for the router.
Old history is dropped first; proposed arguments and the current task are never silently cut.

The smoke check sends synthetic operations to the configured models **without executing the
operations**. It checks relevant reads, credential uploads, destructive injection, unauthorized
writes, unverified scripts, and routine versus complex routing assessments. A missed case exits
with an error and prints the decisions. Run it again after changing model pins or policy.
Passing this small check does not establish resistance to arbitrary adversarial instructions.
In a broader live check, even the 4B model approved a potentially authorized cleanup script
without its contents in the evidence. Use human approvals for operations whose effects you
need to verify independently.

```bash
hermes model sidecars status
hermes model sidecars disable --role guard
hermes model sidecars disable --role router
```

## Tool review

An approval permits the call to proceed through Hermes's existing approval and permission
checks. A denial returns a structured tool error to the agent without prompting the operator.
An escalation uses the existing CLI/gateway approval channel and shows the proposed arguments.
Escalation requires human authorization even when ordinary approval mode is `off` or YOLO is
enabled. Explicit remembered grants cover only the same operation, task, workspace, policy
and profile; they do not override a guard denial. Unattended sessions block without a human
channel or a matching human grant. An unavailable reviewer, malformed
decision, unknown tool contract, or oversized review also escalates.

Review occurs after argument rewrites and before execution. Nested execute_code RPC calls and
calls changed by execution middleware receive their own review; approval of an outer call
does not grant approval to its nested operations. Agent-level memory and context tools are
covered as well as registry tools.

Add an operator policy through normal profile configuration:

```bash
hermes config set approvals.guard.policy "Work in the project workspace. Never upload credentials. Ask before changing infrastructure."
```

The guard is a probabilistic extra check. It supplements deterministic permissions and
approval rules; a model's explanation is not proof that a call is safe or that its description
of the tool is accurate. If a model misses your own representative cases, choose a stronger
pin or leave the guard disabled rather than treating its approvals as a security boundary.

## Configure the routing pool

Add your current model and alternatives. Replace the provider, model IDs, context lengths,
tiers and prices below with values for your own models. These prices are illustrative USD
rates per million tokens, not current vendor tariffs.

```bash
hermes model sidecars pool-add main --provider openrouter --model vendor/your-current-model --tier 3 --context-length 200000 --input-price 3 --output-price 15 --cache-read-price 0.3
hermes model sidecars pool-add fast --provider openrouter --model vendor/your-fast-model --tier 1 --context-length 128000 --input-price 0.2 --output-price 0.8 --cache-read-price 0.02 --latency-ms 1000
hermes model sidecars enable --role router
```

Tier 1 covers routine local work, tier 2 covers multi-step reasoning across components, and
tier 3 covers difficult debugging, architecture and broad coordination. Tiers are assigned
by you. The local classifier assesses the **work remaining now**; it does not invent model
prices or select arbitrary provider names. Repeated failures, repeated identical results,
and empty responses raise the minimum capability tier. The router respects context capacity,
tool support and image support, and stays on the current model when its route is outside the
pool or assessment/resolution fails. Add `--supports-images` for image-capable pool entries.
At the highest tier, repeated failures can select an equally capable peer instead of retaining
the failing model solely because it is cheaper.

Without price overrides, Hermes uses its existing provider pricing resolution, including
subscription-included routes. Models with unknown prices are excluded from cost comparison.
Known cache prices must be supplied for custom endpoints; a missing cache tariff does not
make cached input free. Configure provider credentials through the usual Hermes setup flow.
Use `--base-url` to distinguish custom endpoints and `pool-remove NAME` to remove an entry.

The arithmetic compares:

1. The next call on the current model, with provider-confirmed cached/read-created prefix
   tokens only when the transcript, system prompt, tools, route, credentials and cache lifetime still match.
2. A cold first call on each alternative, charging the **full current context**. A compacted
   provider-side token count cannot make replaying the durable context on another route free.
3. A projected one-to-eight-call horizon, including output/reasoning estimates, growing
   context, published cache tariffs, cache creation premiums and optional latency cost.

New uncached input uses the higher of the input and published cache-write rates. Future cache
hits in the horizon are projections; the next warm prefix comes from observed usage. Estimates
use rough tokenization before a route supplies usage and cannot guarantee a future cache hit.

By default, a downgrade waits three calls after a switch and requires at least 15% projected
savings. A necessary capability upgrade can bypass that cooldown. Automatic switches preserve
the conversation's system prompt and tool schemas, resolve normal provider credentials and
clear token/cache observations from the old route.

Advanced options can be edited with `hermes config edit`:

```yaml
smart_model_routing:
  min_calls_before_switch: 3
  min_savings_ratio: 0.15
  failure_threshold: 2
  latency_cost_per_second: 0.0
```

A positive `latency_cost_per_second` expresses how much USD-equivalent cost you assign to a
second of waiting. Pool `latency_ms` values seed estimates; successful main-agent calls update
the session's observed latency for each model. Zero minimizes estimated provider charges.
Model entries also accept `api_mode`, `supports_tools`, `supports_images`, `cache_min_tokens`,
and `cache_ttl_seconds`. The defaults for cache estimation are 1,024 tokens and 300 seconds;
adjust them to your provider. Routing quotes and their explanations appear in `agent.log`
through `hermes logs`.

## Model choice and validation

The 2026 Qwen3.5 family supports non-thinking inference and large native contexts. Hermes uses
bounded text-only sidecar requests with thinking disabled and strict JSON output. Model cards:
[Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B),
[Qwen3.5-2B](https://huggingface.co/Qwen/Qwen3.5-2B), and
[Qwen3.5-0.8B](https://huggingface.co/Qwen/Qwen3.5-0.8B).
The pinned quantizations come from [Unsloth](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF)
and have matching ModelScope artifacts.

| Model | Q4_K_M download | Use in this implementation |
| --- | ---: | --- |
| Qwen3.5-4B | 2.74 GB | Default tool guard; passed the eight-case local CPU inspection review |
| Qwen3.5-2B | 1.28 GB | Default complexity router; lighter guard option with observed misses |
| Qwen3.5-0.8B | 0.53 GB | Experimental small option; missed unsafe/injected operations in live review |

Select alternatives with `setup --guard-model qwen3.5-2b` or
`setup --router-model qwen3.5-0.8b`; run the smoke check before enabling them. The guard's
default request timeout is 30 seconds and the router's is 15 seconds. Slower CPU hosts can
adjust `auxiliary.tool_guard.timeout` and `auxiliary.model_router.timeout`, up to 120 seconds.
Sidecar failures never invoke the auxiliary cloud fallback ladder. Explicitly choosing a
cloud auxiliary pin sends the review context to that configured provider.

[Qwen3Guard-Gen-0.6B](https://huggingface.co/Qwen/Qwen3Guard-Gen-0.6B) is a useful specialized
content-moderation model, but its safe/controversial/unsafe taxonomy does not establish whether
a tool call is authorized by a particular task. It is not a drop-in replacement for this
reviewer's JSON contract.
[Granite Guardian 4.1 8B](https://huggingface.co/ibm-granite/granite-guardian-4.1-8b) supports
custom judging criteria and requirement checking, but needs more memory and a dedicated
prompt/output adapter; it is not bundled by this command.
