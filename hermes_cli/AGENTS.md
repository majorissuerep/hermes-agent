# hermes_cli/ — local commands, configuration, and updates

Applies on top of root `AGENTS.md`. Public dispatch is `hermes_cli.main`.
Bare interactive `hermes` launches the Ink TUI; `hermes chat -q ...` uses
the native headless agent. `cli.py` and its mixins are internal local slash
dispatch helpers. Do not add a desktop, gateway, daemon, or remote host path.

## Commands

`hermes_cli/commands.py::COMMAND_REGISTRY` owns slash names, aliases, hints,
and completion metadata. TUI-owned session/model operations run in the private
runtime, not a second session-owning shadow CLI. The slash worker may dispatch
configuration/help but must not claim or rebuild its parent's conversation.
Use `goal_command.dispatch_goal_command` for local goal parsing; do not
duplicate parsers in adapters.

## Encrypted self-management

The human unlocks once at terminal startup. The runtime owns a session-scoped
reference to `hermes_security.session_authority`; agents and local children
reuse that capability without another passphrase. Initial Node/Python and
PM/interpreter handoffs use a one-shot inherited key FD where needed. Parent
copies close after spawn; a failed spawn closes them too.

- Never store the passphrase, a raw key, or grants in durable session state,
  argv, logs, transcripts, or config. Scope grants to the exact home/session.
- Bind profile and secret context before subprocess construction, thread hops,
  deferred work, and callbacks. Wrong-home grants are stripped, not borrowed.
- Closing an agent/child releases only its own reference. Closing the owning
  session revokes its authority. An independent session unlocks normally.
- No `--via-host`, inbound RPC, service discovery, or remote credential input.
  Use the local command's existing stdin secret-input mechanism.
- Missing or unreadable config/DB fails closed. Never replace a damaged store
  with empty defaults as an error recovery shortcut.

## Configuration

Settings belong in encrypted `config.yaml`; `.env` is secrets only.

- `DEFAULT_CONFIG` contains settings with actual runtime readers. New keys
  deep-merge automatically; bump `_config_version` only for real migrations.
- `load_config` merges defaults; `load_user_config_effective` performs
  managed overlay, interpolation, and model canonicalization without defaults.
  Presence-sensitive runtime readers use the latter. Do not hand-roll the pipeline.
- Every writer uses `atomic_config_write` (additive, refuses omission loss) or
  explicit `atomic_config_replace` (intentional unset/full replacement).
  Preserve comments, quoting, order, ciphertext, and unreadable-file guards.
- Bare registered secret names route through `config_env_routing` to the
  encrypted secrets writer, never top-level YAML.
- Use `get_hermes_home()` / `display_hermes_home()`, not hardcoded homes.
- Prompt/tool/memory-affecting config or skill changes default to next session.
  An explicit reload must be visible and deliberate; preserve cached prefixes.

## Profiles and ownership

Profiles are independent copies, not live inheritance. Clone to hidden staging
and publish atomically; materialize copied symlinks before writes. Enumeration
never creates homes or deletes marker-less user directories. Rename/delete and
restore use exclusive native maintenance fences; they do not stop/revive services.
Saved historical routing metadata is data to salvage, not execution authority.

## Transactional source update

`terminal_update` keeps the mature prepare → snapshot → source transaction →
PM build → source completion → verify/report pipeline. Do not replace it with
an unguarded git pull. Preserve dirty work, stash debt, every-profile snapshots,
mandatory encryption, original rollback identity, and durable follow-up receipts.

- Arm completion obligations before moving source. Disarm only after verified
  rollback to the original head; don't overwrite another process's debt.
- No pulled code runs in the pre-pull interpreter. PM owns bootstrap/build.
  Preserve key-FD renewal across interpreter exec and one unlock across phases.
- Unsupported downgrade and syntax failures must be rejected before commit.
- Older running updater frames import the names in
  `tests/compat/old_updater_surface.json`. Keep those **proven frozen bridges**
  as takeover/acknowledgment paths, not fake no-op service managers.
- Never restart removed Desktop/gateway processes. Historical serialized fields
  may be read only to finish an old transaction safely.
- Post-commit follow-ups remain visible and retryable; no success message may
  erase an unbuilt environment or unrestored stash.

Test public install/update/repair/resume paths in disposable environments.
Use the canonical runner and native process-identity helpers, never real user
homes, loose PID matches, or argv substring checks.
