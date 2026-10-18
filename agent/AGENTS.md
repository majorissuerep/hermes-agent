# agent/ — native agent, turns, prompts, and compression

Applies on top of root AGENTS.md. The same native AIAgent runs local TUI,
headless, cron, and delegated work. No remote session host owns its turns.

## Find behavior

run_agent.py is the public facade. Construction is agent_init.init_agent;
conversation_loop and turn_* siblings own turn phases. Inline agent-state
tools dispatch through inline_tool_executors, not a new name ladder.
Client lifecycle, session persistence, compression, tool execution, and
provider resolution each have topic siblings. Read the actual constructor
rather than copying an old parameter list.

## Message and cache invariants

- A conversation's system prompt, tools, memories, and existing transcript
  prefix stay byte-stable. Context compression is the exception. New
  instructions enter a new user message or tool result, not a system rewrite.
- Preserve role order, tool-call/result pairing, stable tool-call identities,
  exact API-content sidecars, and reasoning needed by the active protocol.
  Never execute a partial streamed call or replay a completed side effect.
- /steer is an explicit new-user-message boundary, not permission to edit an
  earlier turn. Ordinary reloads default to next session.
- Model/credential routing changes must not rebuild cached prompt state.
  Restore the actual persisted local route on resume, including passive
  historical route decoding; do not infer authority from titles or room labels.
- Persist accepted turns through the native SQLCipher SessionDB. Failure to
  acquire or read durable state is visible and fail-closed, not ephemeral mode.
- Session turn leases and terminal runtime ownership are distinct. The first
  serializes a conversation; the second fences exclusive restore/maintenance.

## Provider and context behavior

Use the bounded built-in catalog and existing runtime/auxiliary resolvers.
A models.dev entry is metadata, not new transport support. Each configured
custom connection owns its endpoint and API dialect; never silently redirect it.

Context pressure includes provider-reported usage, new content, tools,
replayed reasoning, and reserved output. Confirmed smaller input limits win
over speculative catalog values. Output-cap errors do not shrink input windows.
Recheck pressure before a recovered request.

ContextCompressor and its context-engine integration own automatic and explicit
compression. Preserve active requests, tool pairs, durable lineage, resume
identity, and the previous usable history if a summary fails, refuses, truncates,
or times out. Compression fallback must be bounded and honest. Do not add a
surface-specific compressor or remote hygiene layer.

Transient retries honor Retry-After, request/turn budgets, interrupts, fallback
cooldowns, and stream completion. Never turn a provider refusal/auth/billing
error into an endless recovery loop or claim success from empty output.

## Tools, callbacks, and sub-agents

Inline tools use the existing executor table. Local connection cards, clarify,
approval, progress, and streaming callbacks are real TUI consumers; preserve them.
Desktop preview/window/tour/theme callbacks and Bot Chat tools are retired.
Keep the tool schema fixed even when a newly connected MCP server becomes
available through the existing deferred tool bridge.

Delegated agents use direct native construction and their own conversation
state. Acquire the parent's real database path through the registry; fail closed
on persistence failure. Close only owned resources. Cancellation and final
bookkeeping must not revoke the parent or discard an accepted child result.

## Encrypted profile scope

Use agent.session_context and agent.secret_scope. Bind the owning home and
secret scope before callbacks, thread hops, background jobs, and child spawns.
Resolve credentials through get_secret, never ambient process environment for
a routed profile. Profiles remain isolated even in one local process.

A session retains an unlocked key in memory, not a passphrase. Local commands
and sub-agents inherit revocable home/session capability or one-shot key FDs.
The runtime owns its early session reference so /config works before agent
construction; agent references are independently released. Session close and
compression identity transitions must revoke the right authority.

Use existing encrypted atomic state I/O and SQLCipher. Public installed code
and the explicitly excluded regenerable cache roots follow the established
migration policy; never classify prompts, job payloads, memory, or credentials
as a cache to bypass encryption.

## Verification

Use scripts/run_tests.sh with synthetic homes. Resolution/security/I/O changes
need real imports and native persistence. Interactive claims need the public
Ink keyboard path plus actual next-request model/history observation. Protocol
faults, live-provider tests, and cold lifecycle tests prove different things;
record exact SHA, failures, skips, and cleanup instead of adding their counts.
