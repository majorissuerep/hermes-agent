---
name: codex
description: "Delegate coding through an encrypted local session."
version: 1.1.0
author: "Fork maintainers; upstream Hermes Agent contributors"
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [Coding-Agent, Codex, OpenAI, Code-Review]
    related_skills: [claude-code, hermes-agent]
---

# Codex CLI Skill

Use `terminal` to run a focused Codex task under the current encrypted Hermes
profile. The managed runner retains the owning Hermes command and unlock while
Codex keeps its own sandbox and approval policy.

## When to Use

Delegate implementation, investigation, refactoring or review in a selected
working directory. Use `delegate_task` for native Hermes children that need
Hermes tools and encrypted conversation history.

## Prerequisites

- An unlocked local Hermes session on Linux or macOS.
- Codex CLI installed and authenticated through its own login, or an
  `OPENAI_API_KEY` in the selected profile's encrypted secret store.
- A concrete task, working directory, completion condition and permission scope.

Hermes-managed OpenAI OAuth and Codex CLI OAuth are separate logins. Never copy
credential files, export vault keys or ask for a passphrase in chat. Use
`hermes manage schema` to inspect supported runners and management operations.

## How to Run

```python
terminal(
    command="hermes manage run codex -- --sandbox workspace-write 'Inspect the project and report the failing behavior'",
    workdir="/path/to/project",
    timeout=120,
)
```

The runner uses `codex exec --ephemeral`, disables credential-bearing shell
snapshots, and retains a temporary home/session capability. A private kernel-pipe
bridge works with network access disabled; it stores no key file. One-shot mode
does not require a PTY. Do not add another `exec` subcommand.

## Quick Reference

| Need | Mechanism |
| --- | --- |
| Implementation | `--sandbox workspace-write` in a narrow workspace |
| Model choice | `--model MODEL` if the task calls for it |
| Structured events | `--json`; verify actual command results |
| Long task | `terminal(background=true)` and `process_manage` |
| Existing repository review | Ask for a read-only review and inspect its diff |
| Non-repository scratch work | Deliberately select `--skip-git-repo-check` |
| Hermes settings | Child runs `hermes manage schema` or existing public CLI |

## Procedure

1. Inspect the project and instructions with `read_file` and `search_files`.
2. Select the workspace and smallest permission scope that supports the task.
3. Start the managed runner through `terminal`. Put the full task in the prompt
   or pass it on stdin; never include secrets in either.
4. For a background task, keep its returned process id and use
   `process_manage(action="wait", session_id="...")` to collect completion.
5. Inspect output and changed files with native tools. Run the relevant checks
   before accepting the result or publishing any changes.

## Pitfalls

- A zero executor exit is not proof that its shell commands succeeded. Check
  the command result and an independent artifact or state observation.
- Codex may refuse writes outside its workspace, including Hermes settings.
  Add only a deliberately authorized directory with `--add-dir`; retain the
  sandbox. A parent Hermes sandbox still applies and can refuse that access.
- Do not use unrestricted mode as an automatic retry for a refused task.
- The managed runner starts a new one-shot task. It refuses resume/fork modes
  and cannot keep a revoked owner's management access alive.
- Keep external task output in the parent's encrypted history or an explicitly
  requested workspace artifact. Do not create a second plaintext transcript.
- An update changes code for subsequent sessions; it does not replace cached
  instructions in an already-running agent.

## Verification

Require the expected result from an actual tool call, inspect the diff and run
checks. For encrypted self-management, read a fresh non-secret marker through
`hermes config get`, verify the owning file remains ciphertext, and confirm that
an independent command without authority stays locked. A refused or unexecuted
check remains incomplete.

The [official configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
explains environment inheritance, permission profiles and shell snapshots.
