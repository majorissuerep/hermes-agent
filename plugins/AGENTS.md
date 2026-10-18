# plugins/ — extensions at the edges

Applies on top of root AGENTS.md. Plugins add useful capability without growing
core. A plugin must not patch core source files. Ship third-party products in
their own repositories; catalog metadata is not a bundled runtime.

## Supported local extension kinds

General tools/hooks/CLI commands, memory providers, context engines, model
providers, outgoing browser backends, image/video generation, and web tools use
their existing discovery and registration contracts. Messaging platform adapters,
Desktop/dashboard renderers, inbound services, and external cron providers are
retired; do not offer them in the local catalog or resurrect their registrations.

Provider plugins are explicit extensions, not expansion of the built-in provider
support promise. Preserve existing credential records during migration. A model
catalog entry or installable plugin does not prove that connection is qualified.

## Native contracts

- Use the existing PluginManager/context methods and manifest gates. Hooks are
  bounded observers unless an existing documented mutation hook explicitly owns
  the operation. Do not invent hooks without a concrete consumer.
- Keep model prompts, past context, and tool schema pins stable during a live
  conversation. Prompt/tool-affecting installations default to the next session;
  an explicit supported reload is a deliberate cache boundary.
- Bind the owning profile and secret scope before callbacks, jobs, thread hops,
  isolated plugin child launches, and shutdown. Resolve paths at call time.
  Never store a launch-profile home or ambient secrets as a global default.
- Outgoing providers resolve through shared runtime/auxiliary routing. Use the
  existing memory/context-engine interfaces; do not duplicate orchestration.
- Isolated plugin execution is a native child-process protocol, not ASGI,
  remote platform lifecycle, or GUI attachment. Preserve actual supported
  context-method limits, bounded child waits, and termination identity.
- Owned user state uses the existing encrypted writers. Installed plugin code
  remains public code under the established migration policy; do not mix user
  credentials or prompts into that exempt code tree.
- No outbound telemetry without opt-in and no plugin-provided credentials in
  logs, argv, or plaintext managed state.

## Dependencies and catalog

Read pm/AGENTS.md before changing dependencies. Keep upper bounds, SHA pins,
lock provenance, and the dependency quarantine. PM owns install/repair; never
mutate a Hermes environment with raw pip/uv.

Catalog provenance is installer-owned metadata, not a plugin's own claims.
Trust admission and review remain meaningful. Remove entries for unsupported
interface categories rather than displaying broken installation choices.

## Verification

Use the canonical test runner with isolated homes. Test real discovery,
registration, scoped hooks/providers, native child transport, user-state
preservation, and error cleanup. A plugin must have a concrete local consumer;
tests of removed remote products are not retained support obligations.
