# Native terminal troubleshooting

## Startup, settings or encryption

Run `hermes doctor`, `hermes secure-vault status` and `hermes logs`. For supported
ciphertext clobber recovery use `hermes secure-vault repair`; keep damaged originals
and backups until verified. Repair dependencies with `hermes pm repair`, or rerun the
source installer against the same paths if installed code cannot start.

Never fix startup by deleting the user home, copying a plaintext config over encrypted
state, or forwarding a passphrase in chat/env/argv. See `encrypted-self-management.md`.

## Model switching or request failure

Use `/model` in the TUI and check the next turn's selected connection. A session
switch retains history; a one-turn override restores the previous model afterward.
Inspect provider, endpoint, protocol, model ID and context limits together. A model
available behind LiteLLM is not necessarily available through a direct vendor account.

`hermes model` configures defaults; it is not the same as a session-scoped switch.
Reauthenticate only the actual supported provider with `hermes auth --help`. Never
substitute a retired provider, another account or a cached success silently.

## Tools, skills or config changes

Use `hermes tools`, `hermes skills list`, and `hermes mcp test NAME`. Check requirements
without printing secret values. Start a new session for changed tools/system-prompt
state. Do not restart a nonexistent gateway or use `--cli` as a fallback frontend.

## Isolate customization

Use public `--safe-mode` with explicit model/provider when diagnosing profile config,
SOUL/rules or plugins. A malformed config must not be rewritten as empty defaults.
For a selected SSH/container terminal backend, browser/native-computer tools must
execute there or explicitly refuse; silently executing on the local host is a bug.

## Recover conversation state

Close local sessions before restore or profile maintenance. Use real snapshots/full
backups with their documented scope; profile exports exclude credentials. Confirm
SQLCipher integrity, retained messages and a resumed real tool turn after recovery.
A successful process exit alone is not a successful restored conversation.
