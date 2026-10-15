# hermes_cli/ + cli.py — CLI, slash commands, config, skins, updater, profiles

Applies on top of the root `AGENTS.md`. Long-form: `website/docs/developer-guide/cli-internals.md`.

## CLI architecture

`cli.py` holds `HermesCLI` (REPL loop, config, slash dispatch); behaviour lives in mixins
`hermes_cli/cli_commands_mixin.py`, `cli_stream_mixin.py`, `cli_status_bar_mixin.py`,
`cli_billing_mixin.py`, `cli_tui_mixin.py` (widgets, keybindings, panels), `cli_tui_runtime_mixin.py`
(run-loop phases: input dispatch, startup, signals, shutdown), `cli_init_mixin.py` (the `__init__`
phases), ... Module-level helpers live in topical siblings that `cli.py` re-exports:
`cli_config_load.py` (defaults + YAML merge, env mirroring), `cli_render.py` (ANSI/skin colours,
light mode, markdown, `_cprint`, panel wrap), `cli_terminal_input.py` (file drops, paste/Enter-key
sequences, CPR guards), `cli_shutdown.py` (exit watchdog, cleanup steps, one-shot finalize),
`cli_single_query.py` (`-q` runner, exit codes, kanban loops), `cli_auto_maintenance.py` (state-db/checkpoint maintenance).
Moved bodies late-bind cli-level names via `from cli import ...` at call time, so patch seams on the
`cli` facade still intercept them; mutable module state (`_cleanup_done`, `_OUTPUT_HISTORY`,
`_LIGHT_MODE_CACHE`, ...) and every `global`-writing function stay in `cli.py`. **Rich** renders banner/panels; **prompt_toolkit**
handles input + autocomplete; `KawaiiSpinner` (`agent/display.py`) animates API calls and prints
the `┊` activity feed. `load_cli_config()` in `cli.py` merges CLI defaults + user YAML.

`hermes_cli/gateway.py` is the `hermes gateway` facade (process discovery, PM-aware systemd unit
generation/refresh, command dispatch); topical siblings re-exported by the facade include
`gateway_launchd.py` (macOS LaunchAgent backend), `gateway_setup_wizard.py`
(`hermes gateway setup`: `_PLATFORMS` registry, status table, per-platform prompts, service offer),
`gateway_windows*.py`, `gateway_supervised_restart.py`, `gateway_migrate*.py`, `gateway_multiplex_*.py`,
`gateway_enroll.py`, `gateway_command_errors.py`. Sibling bodies read facade names through `_gw()`
(late binding on `hermes_cli.gateway`), so monkeypatch on the facade; mutable state such as
`_resolved_launchd_domain` stays a facade global.
`process_command()` resolves the canonical name via `resolve_command()` then dispatches through
`HermesCLI._SLASH_DISPATCH` (`canonical -> (method name, pass_arg)`), falling back to a
`_handle_<name>_command` method by naming convention. **There is no `elif` ladder — do not add one.**
Skill slash commands (`agent/skill_commands.py`) scan `~/.hermes/skills/` and inject as a **user
message**, never into the system prompt (prompt caching).

Rules: all interactive menu-pickers use curses (`hermes_cli/curses_ui.py`; example
`hermes_cli/tools_config.py`). Never emit `\033[K` (ANSI erase-to-EOL) in spinner/display code —
it leaks as literal `?[K` under prompt_toolkit's `patch_stdout`; space-pad instead:
`f"\r{line}{' ' * pad}"`. Wrapper CLIs extend via the protected hooks in `cli_tui_mixin.py`
(`website/docs/developer-guide/extending-the-cli.md`), not by overriding `run()`.

## Slash command registry (`hermes_cli/commands.py`)

`COMMAND_REGISTRY` (list of `CommandDef`) is the single source; everything derives from it: CLI
dispatch (`resolve_command()`), gateway `GATEWAY_KNOWN_COMMANDS` + dispatch, `gateway_help_lines()`,
`telegram_bot_commands()` (BotCommand menu), `slack_subcommand_map()`, `COMMANDS` (autocomplete),
`COMMANDS_BY_CATEGORY` (`show_help()`). Fields: `name` (no slash), `description`, `category`
(`Session | Configuration | Tools & Skills | Info | Exit`), `aliases` tuple, `args_hint`,
`cli_only`, `gateway_only`, `gateway_config_gate` (config dotpath; a `cli_only` command becomes
gateway-available when truthy — `GATEWAY_KNOWN_COMMANDS` always includes gated commands so the
gateway can dispatch them; help/menus show them only when the gate is open).

**Adding a command:** (1) `CommandDef("mycommand", "What it does", "Session", aliases=("mc",),
args_hint="[arg]")` in `COMMAND_REGISTRY`; (2) `_handle_mycommand_command(self, cmd_original)` on the
relevant `cli_*_mixin.py` (picked up by convention) or an explicit `_SLASH_DISPATCH` entry
`"mycommand": ("_handle_mycommand", True)` when the method name/arg-passing differs; (3) for the
gateway, `_handle_mycommand_command(self, event)` on the matching `gateway/slash_commands_*.py`
mixin and list it in `_IDLE_COMMANDS` (or `_PLAIN_COMMANDS` if it must work mid-run) in
`gateway/run_busy.py` — handlers resolve by name via `_command_handler_table`; (4) persistent
settings via `save_config_value()` in `cli.py`. **Adding an alias** = add to `aliases`; every
surface updates automatically. Commands that mutate system-prompt state default to deferred
invalidation with `--now` opt-in (root invariant).

### Shared goal commands

`hermes_cli/goal_command.py::dispatch_goal_command` owns `/goal` parsing and manager
mutations. CLI, messaging gateway, TUI/Desktop/dashboard and Desktop goal controls
all delegate there; adapters only resolve sessions, authorize gate creation, render
results, and schedule kickoff/continuation prompts. Async callers preserve ContextVars
when running dispatch off-loop (drafting uses profile-scoped auxiliary credentials).
Do not add a surface-specific goal parser. ACP has no goal command or goal loop yet.

## Config system (`hermes_cli/config.py`)

### Agent-driven operations in the encrypted fork

An unlocked session lends temporary, home-scoped authority to local CLI and delegated
children through `hermes_security/session_authority.py`. The passphrase is never
retained or exported. Local subprocess builders select the active session's grant,
strip wrong-profile grants, and PM build workers strip authority entirely. Closing
the owning agent revokes its grant; closing a subagent must preserve its parent's.
Before an agent exists, detached chat gateways inherit the launcher's unlock through
the existing one-shot kernel pipe; interpreter selection must renew that pipe across exec.
Use ordinary CLI config writers for self-management, never plaintext state edits.
MCP's `hermes mcp <action> --via-host` remains an alternative through existing
`mcp.servers.*` RPCs, scoped to the same authenticated home. Credentials enter
through `set-api-key --value-stdin`, never argv. Mutations default to the next session
without rebuilding live prompts or tool arrays. Live contracts belong in the
session-authority tests and encrypted lifecycle VM suite.

- **config.yaml option:** add to `DEFAULT_CONFIG`. Bump `_config_version` ONLY to actively
  migrate/transform existing config (rename keys, restructure); new keys deep-merge automatically.
  Top-level sections (non-exhaustive): `model, agent, terminal, compression, display, stt, tts,
memory, security, delegation, smart_model_routing, checkpoints, auxiliary, curator, skills,
gateway, logging, cron, profiles, plugins, honcho`. `auxiliary` = per-task side-LLM overrides
  (`agent/AGENTS.md`); `curator` = `enabled, interval_hours, min_idle_hours, stale_after_days,
archive_after_days, backup.*`.
- **.env = SECRETS ONLY** (keys, tokens, passwords): add to `OPTIONAL_ENV_VARS` with
  `{"description", "prompt", "url", "password": True, "category": provider|tool|messaging|setting}`.
  Non-secret settings go in config.yaml; if internal code needs an env mirror, bridge it in code
  (`gateway_timeout`; `terminal.cwd` → `TERMINAL_CWD`). `MESSAGING_CWD` is removed and `TERMINAL_CWD`
  in `.env` is deprecated — the loader warns; canonical is `terminal.cwd`. `hermes config
set/get/unset <NAME>` route any bare name registered in `OPTIONAL_ENV_VARS` / `_EXTRA_ENV_KEYS`
  (or carrying a `setup_hidden_env` platform suffix) to `.env` via `config_env_routing.py` — the
  file the platform setup flows write — never to the top level of config.yaml.
  Migration normalization lives in `config_env_sanitize.py`; it must use the
  envelope reader before the shared writer, never decode ciphertext as text.
- **One writer seam.** Every write of a `config.yaml` (main or profile) goes through
  `hermes_cli.config.atomic_config_write` (refuses deletion by omission) or the explicit
  `atomic_config_replace` full-state path (→ `utils.atomic_roundtrip_yaml_save`, ruamel
  round-trip): comments, key order, quoting and blank lines survive, and the fail-closed unreadable-file
  guard runs first. Deliberate `pop()`/unset/migration paths use `atomic_config_replace`; additive
  writers stay on `atomic_config_write`. Never call
  `atomic_yaml_write` / `yaml.dump` / `yaml.safe_dump` on a config path — `scripts/check_config_yaml_writers.py`
  (CI lint) rejects it, and `tests/hermes_cli/test_config_yaml_comment_preservation.py` guards each
  path (#92554). The commented example blocks are appended only when the file is created.
- **Three loaders — know which you're in:** `load_cli_config()` (CLI, `cli.py`); `load_config()`
  (`hermes tools/setup`, most subcommands, `hermes_cli/config.py`, merges `DEFAULT_CONFIG`);
  `hermes_cli/config_effective.py::load_user_config_effective()` (gateway runtime via
  `gateway/run.py::_load_gateway_config`, TUI gateway `_load_cfg`, cron, `hermes send`, doctor,
  `hermes_time`/`hermes_logging`: user file + managed overlay + `${VAR}` expansion + model-key
  canon, NO defaults — for presence-sensitive readers). If the CLI sees a key and the gateway
  doesn't (or vice versa), you're on the wrong loader — check `DEFAULT_CONFIG` coverage. Never
  hand-roll raw-read → overlay → expand; `read_user_config_raw` is for write-back round-trips only.
- **Every `DEFAULT_CONFIG` key has a runtime reader, and every reader a registry entry.** Both drift
  modes are silent: a registered key nothing reads (a knob that does nothing) and a reader of a key
  never registered (never shown, never migrated; new roots also go in `_KNOWN_ROOT_KEYS`). For a new
  key: `rg -n '"<key>"' hermes_cli/config_defaults.py` AND `rg -n '<key>' --glob '!tests' .` both hit,
  and one invariant test sets it in a temp `config.yaml` and asserts the behaviour through the loader
  the consuming surface uses. Under multiplex the process env never overrides a profile's YAML.
- **Working directory:** CLI uses `os.getcwd()`; messaging uses `terminal.cwd`, bridged to
  `TERMINAL_CWD` for child tools.

## Skin engine (`hermes_cli/skin_engine.py`)

Skins are **pure data** (`SkinConfig`); no code change to add one. `init_skin_from_config()` reads
`display.skin` at startup; `get_active_skin()` (cached), `set_active_skin(name)` (`/skin`),
`load_skin(name)` (user `~/.hermes/skins/*.yaml` → built-ins → default; missing values inherit
from `default`). Built-ins in `_BUILTIN_SKINS`: `default`, `ares`, `mono`, `slate`. Keys: `colors.*`
(banner border/title/accent/dim/text, response_border), `spinner.*` (waiting/thinking faces,
thinking_verbs, wings), `tool_prefix`, `tool_emojis`, `branding.*` (agent_name, welcome,
response_label, prompt_symbol). Consumers: `banner.py`, `display.py`, `cli.py`. Key-by-key table
and YAML template: `website/docs/user-guide/features/skins.md`.

## Update pipeline (`hermes update`)

Transactional, `plan → snapshot → apply → restart-per-kind → verify → report`; every stage guards a
real field failure (fleet campaign #91277), and a PR that weakens a stage answers for that class.
The stage-by-stage contract: `website/docs/developer-guide/cli-internals.md` § Update pipeline.

- Snapshots cover EVERY profile with one identical file set (never partial or tiered); they are
  file-loss recovery, `--backup` owns rollback.
- The Windows ZIP fallback fires only when git itself failed, refuses a dirty tree, and grafts the
  `_ZIP_PRESERVED_NESTED` build outputs into the swap.
- Restarts are fleet-wide and drain-first, bounded by the home each unit runs on
  (`update_fleet_scope.py`); another install's runtime is never restarted.
- Post-commit failures are owed follow-ups, exit 0 (stash left 1, Ctrl-C 130);
  receipts in ROOT `logs/update_receipts/`.
- Nothing runs pulled code in the pre-pull interpreter. `update_handoff.py` and
  `update_serve_obligations.py` are a FROZEN compat surface (`tests/compat/old_updater_surface.json`):
  removing a name bricks releases mid-update.
- Read #92091 (gateway-owned control socket) before adding any process-scan heuristic.

## Profiles (multi-instance)

Before changing profile creation, enumeration, multiplex migration or service lifecycle,
read `website/docs/developer-guide/cli-internals.md#profile-and-supervisor-lifecycle-contracts`.
The detailed contracts live there; the invariants here apply to every edit:

- Profiles are independent islands, never live-inheriting configuration. `--clone` copies
  configuration but strips adapter-owned channels unless `--clone-channels` is explicit.
- Bind profile context for each activity. The multiplex process environment is its launch
  profile, not the active conversation. Global caches key on `hermes_home_key()`.
- Publish clones atomically from hidden staging directories; materialize symlinked config
  and secrets before writing. Never create a home while enumerating or serving it.
- Only non-tombstoned homes with identity markers are live. All surfaces use the same
  identity predicate; a marker-less directory may hold user files and is never deleted.
- Multiplex mode is resolved once per boot; readers use the live gateway verdict, not a
  merged config default. Auto-migration preserves its unfinished manifest and guard set.
- Services carry their owning home explicitly. Preserve user/system/launchd-domain identity
  on refresh, and use `(pid, start_time)` plus canonical command matchers for liveness.
- A gateway with an external supervisor restarts through that supervisor; never replace it
  with a foreground daemon. Fleet restart remains scoped to the updated install.

## Native CLI deck writes

The classic REPL is a gateway client, not a second state.db writer. `cli_deck_mixin.py` uses
`GatewaySessionStore.deck_open` / `deck_close`, translated to authorized, revision-fenced
`session.mutate` deck receipts. Deck polling uses the facade's read-only reader. Do not open
`session_deck.open_db()` in an interactive native frontend; standalone deck commands and the
session host have their own ownership paths. The store shares transaction-level deck SQL with
the legacy host, so registration remains idempotent across compaction and retries.

## Legacy Nous accounts (`hermes_cli/anon_auth.py`)

Nous is not bundled as an inference provider. First-run setup selects from the
supported provider catalog; welcome claims require an explicitly installed
provider plugin. Existing account records remain readable for managed tools
and recovery. `hermes portal` defaults to account information, not model setup.

`anon_auth.run_sign_in()` is the one sign-in composition: it yields `SignInState`s that carry their
own `.copy` / `.copy_terminal`, persists only after a completed promotion AND a token grant, and runs
`settle_after_upgrade` exactly once per completion. `hermes auth upgrade`, the CLI and gateway
`/login` and the desktop poller are renderers over it; the desktop's plain device-code login
(`_nous_plain_poller`) stays a separate path. Long form: `cli-internals.md` § Nous free tier sign-in.
