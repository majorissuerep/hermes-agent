# tools/ — model tools and terminal execution

Applies on top of root AGENTS.md. Prefer existing tools, CLI + skill, or plugins
over new core surface. Every core schema is paid for on every model request.

## Registry

Each tools/*.py can register through tools.registry.registry; package tools
register from tools/<package>/tool.py. model_tools discovers these modules.
Handlers return JSON strings or the existing native multimodal envelope.
A failed import must not silently remove a promised tool from qualification.

check_fn answers reachability and opt-in under the owning profile, not UI
identity. Use scoped get_secret and profile-keyed availability caches.
No Desktop-only toolsets, preview/window controls, messaging tools, or remote
management brokers belong in this terminal-only fork.

A new core tool needs both registration and a real toolset consumer. Follow
the existing table-driven dispatch and setup UX. Avoid schema references to
unavailable toolsets; use the established dynamic cross-reference assembly.

## Local scope and paths

- Resolve state paths at call time with get_hermes_home; display paths with
  display_hermes_home. Do not capture the launch home at import time.
- Bind profile/secret/session context across worker threads and subprocesses.
  A missing scoped credential is not permission to borrow an ambient one.
- Model-facing file reads enforce agent.file_safety. Managed config, secrets,
  history, job payloads, and outputs use existing encrypted I/O, not raw edits.
  Preserve the documented installed-code/regenerable-cache exclusions.
- Session-aware approval queues are local TUI state. A pending request needs a
  real responder, bounded wait, revocation, and per-request correlation.
  Silence is not consent. Headless and cron policy is explicit.
- Ask/approve preparation may run concurrently; shell effects remain ordered
  and every actual command rechecks guards. A failed earlier command invalidates
  consent collected for a later command's previous world state.

## Terminal and processes

Use existing terminal environments for local, container, or outgoing SSH work.
Do not confuse an outgoing execution backend with an inbound management gateway.
Never silently execute on the host after a selected sandbox refuses.

Machine facts, executable lookup, native process identity, and restart-safe
child dispatch belong in hermes_platform. Destructive signals require a verified
PID/start-time identity. Do not infer identity from argv substrings.
Keep working directory, shell state, environment scrubbing, timeouts, EOF/PTY
semantics, process notification ownership, and cleanup contracts intact.

Local Hermes subprocesses inherit temporary session authority without a second
passphrase. Never serialize raw keys or passphrases, and never forward local
vault authority to outgoing remote execution backends. Close inherited FD copies
after spawn and on errors.

## Delegation and media

delegate_task uses direct native agents, the local registry, and encrypted
delegation accounting. Child completion/control follows the parent's real
conversation lineage and exact local transport where applicable. Do not revive
worker RPC, async gateway delivery, Bot Chat, or remote host ledgers.

Browser tools use configured native/outgoing browser/CDP backends. Shared remote
Bot Screens, VNC leases, and extension-control management brokers are retired.
Native vision output is conversation history, not a one-request attachment;
route images through the shared vision helpers and budget the replayed payload.

MCP remains an outgoing client capability. Preserve authentication, trust,
bounded discovery, scoped server reuse, deferred schema pins, and encrypted
credential storage. Do not introduce an inbound MCP server or --via-host path.

## Verification

Behavior contracts go in tests/tools and related mirrored agent tests. Use
scripts/run_tests.sh, never bare pytest. Native process tests own their actual
children; product E2E uses disposable homes/containers/pods. Tests must verify
observable execution, routing, ciphertext where policy applies, ownership, and
failure cleanup—not source text or a success banner.
