# Hermes Agent — Development Guide

Hermes is a **local terminal agent for Linux and macOS**: an Ink TUI, direct
headless commands, and self-called terminal sub-agents. It retains encrypted
history, memory, skills, tools, profiles, and explicit local scheduled work.
Desktop, dashboard, messaging gateway, shared remote session hosts, ACP, and
inbound management servers are not part of this fork.

**Never give up on the right solution.**

## Product and design invariants

- Reliability, encrypted self-management, and install/update/restore take priority
  over integration count. The bounded provider catalog and qualification contract
  are in `website/docs/developer-guide/reliability.md`.
- **Prompt caching is sacred.** Do not rewrite past context, system prompts,
  memories, or tool schemas mid-conversation. Context compression is the exception.
  Prompt-affecting changes default to the next session; an explicitly supported
  `--now` boundary requires deliberate user selection.
- **The core is a narrow waist.** Extend existing code, then prefer CLI + skill,
  a service-gated tool, or a plugin. Every core model tool costs tokens every turn.
- Preserve user data, including retired provider credentials and old transcripts.
  Passive schema readers and externally frozen updater bridges do not authorize
  restoring removed services or pretending obsolete operations succeeded.

## Workspace and verification

Use an isolated `HERMES_HOME` and `HERMES_RUNTIME_DIR` before provisioning with
`source ./activate`. Use PM-owned environments; never mutate them with raw pip/uv.
Read `CONTRIBUTING.md` and the relevant area's `AGENTS.md` before editing.

- Tests: **`scripts/run_tests.sh`**, never bare pytest. Write behavior contracts,
  preferably 1–2 invariant tests per bug, red on the base. No source-reading or
  change-detector tests; no tests writing to an installed user's home.
- Host-specific behavior runs on that native host with
  `@pytest.mark.platforms(...)`; never fake `sys.platform`.
- Product qualification uses disposable containers/pods/VMs with synthetic homes,
  real imports, public entry points, real keyboard input, and wire-observed model
  routing. Unit counts, skips, retry-masked failures, and banners are not E2E proof.
- `python scripts/check` runs all blocking CI lint checks with pinned tools.
  `# noqa` does not waive code health; a narrowly justified
  `# health: allow RULE -- why` does.
- Rebase onto current `main` before merging. Test the exact candidate SHA and
  distinguish Linux, macOS, protocol, live-provider, and lifecycle evidence.

## Local runtime and encrypted authority

`hermes_cli.main` dispatches commands; bare interactive `hermes` launches
`ui-tui/`. Node starts its private Python child `tui_runtime` over stdio.
There is no listener, attach endpoint, sidecar, or remote session authority.
`cli.py` is an internal slash-command backend, not a second public REPL.

- Local TUI/headless owners use native `AIAgent` and SQLCipher `SessionDB`.
  Profile runtime ownership permits cooperating local processes but fences
  exclusive restore/maintenance. Never unlink a live lock inode.
- The human unlocks once. Retain the unlocked key in memory, **not the passphrase**.
  Local child calls use home/session-scoped revocable capabilities or inherited
  one-shot kernel FDs. Never put passphrases or raw keys in argv, environment,
  transcripts, config, logs, or persistent session state.
- Bind the owning profile and secret scope explicitly in thread hops, callbacks,
  background tasks, and child spawns. Never borrow the launch profile's ambient
  secrets. Closing a child must not revoke its parent's authority.
- Persist owned config/auth/history through existing encrypted atomic writers.
  A missing or unreadable store fails closed, never to empty config or an
  ephemeral substitute. Plaintext recovery is an explicitly requested offline
  export, not disabling encryption in place.

## Code structure and contribution rules

`run_agent.py` and `hermes_state/__init__.py` are facades; behavior belongs in
topic siblings. Late-import facades inside functions to avoid cycles. Patch the
seam production reads. No re-export shims for internal moves; fix imports and
guides in the same change.

- New functions: CC ≤ 20, ≤ 300 lines, nesting ≤ 6; files ≤ 2,000 lines.
  Already over-limit units may only shrink; move behavior into cohesive siblings.
- No defensive clutter, dead flags, silent `except: pass`, speculative hooks,
  or features wired without a concrete consumer and behavior proof.
- Paths use `get_hermes_home()`; display text uses `display_hermes_home()`.
  Do not hardcode `~/.hermes`. Profiles are independent islands.
- Machine facts, executable resolution, process identity, and native child
  supervision belong in `hermes_platform`. Never identify processes by argv
  substrings; use its structured command parser and PID/start-time identity.
- Dependencies have upper bounds; git/Actions pins use SHAs. After pyproject
  changes run `hermes pm lock` and commit the lock. Read `pm/AGENTS.md`.
- TypeScript uses feature-owned nanostores, thin roots, narrow hooks, colocated
  actions, interfaces for shared shapes, and table-driven dispatch.
- No outbound telemetry without opt-in. `SECURITY.md` defines security scope;
  §3.1 reports stay private, §3.2 hardening is ordinary public work.
- Verify both the bug and intent with history before changing an isolation
  boundary. Never fix a feature by destroying it or silently redirecting requests.

## Routing

| Area | Guide |
| --- | --- |
| Agent core and compression | `agent/AGENTS.md` |
| Commands, setup, profiles, updater | `hermes_cli/AGENTS.md` |
| Private stdio runtime and Ink TUI | `tui_runtime/AGENTS.md` |
| Model tools and terminal backends | `tools/AGENTS.md` |
| Plugins | `plugins/AGENTS.md` |
| Skills and curator | `skills/AGENTS.md` |
| Local cron and kanban | `cron/AGENTS.md` |
| Tests | `tests/AGENTS.md` |
| Dependencies | `pm/AGENTS.md` |
| Host facts and process helpers | `hermes_platform/AGENTS.md` |

Long-form design and operating guides live in `website/docs/`.
