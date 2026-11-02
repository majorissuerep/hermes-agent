---
title: "Claude Code — Delegate to Claude Code through an encrypted local session"
sidebar_label: "Claude Code"
description: "Delegate to Claude Code through an encrypted local session"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Claude Code

Delegate to Claude Code through an encrypted local session.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/autonomous-ai-agents/claude-code` |
| Version | `2.3.0` |
| Author | Teknium, Hermes Agent; fork maintainers |
| License | MIT |
| Platforms | linux, macos |
| Tags | `Coding-Agent`, `Claude`, `Anthropic`, `Code-Review` |
| Related skills | [`codex`](../../bundled/autonomous-ai-agents/autonomous-ai-agents-codex.md), [`hermes-agent`](../../bundled/autonomous-ai-agents/autonomous-ai-agents-hermes-agent.md) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Claude Code Skill

Use `terminal` for a focused Claude Code task under the current encrypted Hermes
profile. The managed runner selects the owning Hermes installation, lends the
session's temporary authority and disables the extra Claude conversation store.

## When to Use

Delegate implementation, investigation or review to Claude Code. Use
`delegate_task` for native Hermes children that need Hermes tools and their own
encrypted Hermes conversations.

## Prerequisites

- An unlocked local Hermes session on Linux or macOS.
- Claude Code installed and authenticated through its own login, or an
  `ANTHROPIC_API_KEY` in the selected profile's encrypted secret store.
- A concrete task, directory, budget and allowed tools.

Claude Code OAuth and Hermes-managed Anthropic OAuth are separate logins.
Never copy credential files or put keys, grants or passphrases into prompts,
command arguments, configuration files or transcripts.

## How to Run

```python
terminal(
    command="hermes manage run claude -- --allowedTools Read 'Review this project and describe the implementation'",
    workdir="/path/to/project",
    timeout=120,
)
```

The runner adds `--print --no-session-persistence`. It passes stdin and stdout
through, so print mode needs no PTY. Do not start an interactive session or add
another print flag. The current profile's temporary unlock is already present
for permitted local Hermes calls.

## Quick Reference

| Need | Mechanism |
| --- | --- |
| Read-only investigation | `--allowedTools Read` |
| A specific management read | `--allowedTools 'Bash(hermes config get:*)'` |
| Edits | Select only the required Claude tools and workspace permissions |
| Model choice | `--model MODEL` when required |
| API budget | `--max-budget-usd AMOUNT` for an API-backed task |
| Structured events | `--output-format stream-json --verbose` |
| Long task | `terminal(background=true)` and `process_manage` |
| Native Hermes management | Child runs `hermes manage schema` |

## Procedure

1. Read project instructions with `read_file` and identify the completion test.
2. Select a narrow work directory and allowed tools. Do not auto-approve all
   Bash commands to make a read-only management task work.
3. Run the managed one-shot through `terminal` with the complete task. For long
   work, keep the returned process id and use `process_manage` to wait or stop.
4. Inspect Claude's tool results and actual changed files with native tools.
5. Run the relevant checks. Accept or publish work only with the required
   evidence and authorization.

## Pitfalls

- A successful CLI exit can contain a denied or failed tool call. Inspect the
  tool result and an independent outcome before reporting completion.
- Do not retry with permissions disabled when a shell or file operation is
  refused. The parent Hermes sandbox and Claude's permissions still apply.
- The managed runner refuses background/resume modes owned by Claude itself.
  Hermes owns the background process and the temporary authority's lifetime.
- Keep output in the parent's encrypted history or a requested workspace
  artifact. Do not add a plaintext session log or an external transcript store.
- Closing a child leaves the parent usable; ending the original owner revokes
  derived management authority.
- Configuration, memory and skill changes apply to the next Hermes session
  unless the user deliberately selects a supported immediate boundary.

## Verification

Check the actual shell result, changed files and relevant tests. A management
smoke test reads a fresh non-secret marker through `hermes config get`, keeps
configuration ciphertext on disk and refuses an independent locked command.
The print-mode banner or Claude's completion prose alone is insufficient.

See the [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)
for flags supported by the installed version.
