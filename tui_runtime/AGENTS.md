# Local terminal runtime + Ink UI

Applies on top of root `AGENTS.md`. The public interactive product is the Ink TUI.
The Python backend is a private child of that terminal, never a listener or shared daemon.

## Ownership and transport

```
hermes → Node (Ink) → python -m tui_runtime
                         └─ AIAgent + SQLCipher SessionDB + terminal subagents
```

Node owns the screen. Python owns conversation state, model calls, slash behavior and tools.
Newline-delimited JSON-RPC travels over the child's stdin/stdout; diagnostics use stderr.
There is no WebSocket, remote attachment, discovery, sidecar, Desktop or messaging transport.
An exited runtime rejects pending work and stays stopped; it must not silently create another owner.

The launcher unlocks once and uses `hermes_security.handoff.prepare_child_key` to pass a sealed
key descriptor. Node remaps that descriptor into its private Python child, then closes its own copy.
`python -m tui_runtime` adopts the descriptor before importing stateful modules. Never pass a
password or key through argv, environment values or files. The inherited descriptor is authority,
not an invitation to reopen an independent session. Terminal subagents retain the existing scoped
broker and child-key handoff; do not replace them with a network control service.

SessionDB/config opens fail closed. Do not substitute an ephemeral DB or `{}` after an encrypted
state failure. Runtime ownership and active-session leases fence competing local writers; restore
uses exclusive maintenance. Profile scopes must be explicitly bound across thread hops and child
spawns; use existing context-thread and served-profile child environment helpers.

## Implementation shape

`server.py` is a facade; behavior lives in topical `methods_*.py` siblings rebound through
`method_ctx.bind_module`. Late imports through that facade are the patch seam. Do not add a
method-name conditional ladder. Session lifecycle, direct agent construction, prompt execution,
model switching and local notification handling have separate siblings.

`contracts/` declares method params/results, events and server→client questions. Regenerate
`apps/shared/src/gateway-contract.generated.ts` and `.openrpc.json` with
`scripts/gen_gateway_contracts.py`. The historical generated filenames do not expose a remote API.
Unknown parameter keys are rejected; strict test mode validates emitted payloads/results.
New blocking user questions need a Python contract plus Ink's `createServerRequestHandler` handler.
Responses carry the original request id; cancellation clears only the corresponding overlay.

The renderer uses feature-owned stores and thin hooks/actions. `localAgentClient.ts` owns the
subprocess and private channel. `/model` menus and typed commands share `config.set`, which uses
real `AIAgent.switch_model`; session/global/one-turn reasoning scope must travel with the route.
Model changes preserve conversation history and the cached prompt prefix. Never rebuild the
system prompt merely because settings changed during a conversation.

## Verification

Use canonical `scripts/run_tests.sh`, isolated temporary homes and PM-managed test environments.
Application/PTY qualification runs only in disposable Linux pods with no host homes, mounts or
credentials. Static codegen, TypeScript and fixture-only subprocess tests may use the isolated
build toolchain. Real Ink tests must render the menu, send keyboard input, make a next turn and
assert HTTP wire model/history; handler mocks are not a substitute.
