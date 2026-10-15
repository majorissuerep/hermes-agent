---
sidebar_position: 15
title: "CLI Internals"
description: "How hermes_cli is shaped: slash dispatch, config loaders, the skin engine, the transactional update pipeline, and process-identity rules"
---

# CLI Internals

Companion to `hermes_cli/AGENTS.md` (the rules) — this page holds the longer explanations.

## Update pipeline

User-facing behaviour (receipts, `--plan`, snapshot modes) is in
[Updating](../getting-started/updating.md); `hermes_cli/AGENTS.md` carries the short form of the rules.

Fleet-update campaign #91277 (Aug 2026). A PR that weakens a stage must answer for the failure class
it guards. `plan → snapshot → apply → restart-per-kind → verify → report`

- **Plan** (`update_inventory.py`, `hermes update --plan`): read-only inventory — install kind, all
  profiles, every live gateway with supervisor + running code version. Deployment kinds are
  first-class: `git` updates in place; `docker`/`nix`/`apt` are NOT in-place-updatable and the
  updater reports the correct external command instead of fighting the deployment model.
- **Snapshot** (`backup.py`): pre-update quick snapshot for EVERY profile (the code swap + fleet
  restart touch all of them), each into its own `state-snapshots/`, identical file set, 1 GiB
  per-file cap, keep=1. **Never add a partial/tiered snapshot set** — mixed coverage creates
  torn-restore states across schema generations. Quick snapshots are FILE-LOSS RECOVERY (the
  per-profile cron-jobs safety net restores from them), NOT code-rollback insurance; `--backup`
  full mode owns rollback.
- **Apply**: git pull, or the Windows ZIP fallback — which fires ONLY when git itself failed
  (`_should_zip_fallback_on_update_error`, argv-classified; a dependency-install failure must never
  trigger a tree-clobbering re-download), REFUSES a dirty working tree (`-uall` + a pre-swap TOCTOU
  re-check — but classifies a `!!` line by whether the swap would destroy it: an ignored path under a
  root entry the ZIP does not ship (`.bytecode-fingerprint`, `.hermes-bootstrap-complete`,
  `hermes_agent.egg-info/`; tracked root entries stand in for the ZIP set before the download, the
  re-check gets the real one), a nested `__pycache__`/`node_modules`, or a `_ZIP_PRESERVED_NESTED`
  output is admitted; other ignored files under shipped dirs still block), and grafts the live nested
  build outputs (`_ZIP_PRESERVED_NESTED`: `apps/desktop/{release,dist,node_modules,build}`,
  `hermes_cli/web_dist`, `ui-tui/{dist,node_modules,packages/hermes-ink/dist}`, `web/node_modules`,
  `scripts/whatsapp-bridge/node_modules`) into the staged swap by hardlink (the GitHub source ZIP has
  none of them; without the graft the swap deletes them). Post-swap, the Desktop
  rebuild decision also trusts the build stamp under HERMES_HOME, so an install that already lost
  its artifacts in an earlier update is rebuilt instead of "forgotten" (#90495).
- **Restart-per-kind**: systemd and launchd restarts are FLEET-WIDE within the updating install (every
  `hermes-gateway*` unit / `ai.hermes.gateway*` LaunchAgent whose home is the updating root or one of its
  `profiles/<name>`), drain-first (SIGUSR1), with per-unit/per-label failure isolation. Restarting only the
  invoking profile's service leaves siblings on stale `sys.modules` until they crash — the largest dupe-PR
  cluster in the repo's history came from that bug. The fleet is bounded by HOME, not by namespace:
  `hermes_cli/update_fleet_scope.py` judges every unit/label/process by the home it actually runs on
  (live environ, unit `Environment=`, plist `HERMES_HOME`), and a runtime of another `HERMES_HOME` on the
  same account — a sibling install, the real `hermes-gateway.service` seen from a scratch home — is named and
  left alone, never restarted (#93349).
- **Verify**: gateways stamp `code_sha`/`code_version` into `gateway_state.json` on every
  runtime-status write (`gateway/status.py`); the updater compares each live gateway against the
  fresh checkout and prints a fleet version matrix. After the commit point (the tree moved) nothing
  fails `hermes update` (exit 0 unless the code was rolled back): a provably-stale or stopped
  gateway, a failed build/launcher/config migration/bytecode sweep/channel adoption/Windows resume
  each print `⚠`, land in the receipt as `outcome: "success"` with `followups: [{step, reason}]`,
  and keep their own obligation armed (`source-completion-pending` for the tail, the fleet-restart
  obligation for gateways) so the next launch or `hermes update` — including "Already up to date" —
  retries them. The owed-fleet-restart CLI startup warning is what keeps a mixed-version fleet from
  ever looking healthy. A completion process that could not be started (temporary directory,
  request file, spawn) or died without a correlated result (OOM, SIGKILL) is the same kind of
  debt: an owed `completion` follow-up with the source-update tail re-armed, exit 0. Exit 2
  (refused/concurrent) and exit 1 (nothing committed / rolled back) keep their meaning; the one
  committed run that exits 1 is an autostash whose restore conflicted and stays parked: the
  receipt is `partial` with a `user_action`, since nothing retries what only the user can re-apply
  (#122557).
- **Report**: every run writes a machine-readable receipt to the ROOT home's
  `logs/update_receipts/` (never a sticky profile's; `update.log` likewise) — `latest.json` pointer;
  steps, skips WITH reasons, restart outcome, plan, fleet snapshot, `followups`. The receipt is on
  disk as `outcome: "running"` from the start and refreshed at each stage boundary; the next
  update marks a `running` record whose processes are gone `interrupted` and says so. Readers
  (`hermes logs update`, debug bundle, dashboard status, pm receipts) use the root home too.
  A dependency-sync failure in the completion bootstrap is a `dependencies` follow-up (A6), and
  Ctrl-C after the commit point finalizes `interrupted` (exit 130), never `failed`.
  Before a source swap, the parent captures plan/snapshots/receipt and its Windows pause token.
  `update_completion.py` runs new-code PM preparation with site initialization disabled, then
  selected-Python builds, maintenance, scans/restarts and verification. Git/current/ZIP share
  this owner; never reload or purge modules to continue in the old interpreter. The parent keeps
  the lock, waits, and accepts only a correlated terminal result. `cmd_update` still finalizes
  early failures and missing/killed-child outcomes; PM refusal data survives the handoff.
  See `website/docs/developer-guide/source-update-completion.md`. A begun-but-unwritten receipt is a bug.
- **Nothing runs pulled code in the pre-pull interpreter.** This tree finishes updates through
  `update_completion.run_completion` / `_update_takeover.py`.
  `hermes_cli/update_handoff.py` and `hermes_cli/update_serve_obligations.py` are the
  FROZEN COMPAT SURFACE for releases that lazily import those module
  names from the NEW tree after the checkout swap.
  Keep their public names importable and behavior-preserving. They call into
  `_old_updater.stop_for_relaunch` → `_run_child`. Removing a name
  bricks every release mid-update. see `tests/compat/old_updater_surface.json`.

Process-scan coordination between updater, serve/dashboard, and gateway is being replaced by a
gateway-owned control socket (#92091); scans are the fallback layer for old/crashed processes — read
#92091 before adding any heuristic. Process identity rules (never argv substrings; canonical
matchers; parser-derived flag sets; never blanket-exclude gateway ancestors, #87594): root
`AGENTS.md` and `website/docs/developer-guide/cli-internals.md`.

The systemd blunt-restart fallback waits for the unit's `TimeoutStopUSec` plus
`TimeoutStartUSec`, with 15 seconds of client-side slack. It reads the target unit
in the same manager scope as the restart; both the initial attempt and retry use
this budget, including the catch-up restart after an interrupted update.
A start after a graceful drain uses only the start budget plus slack.
A missing, unparseable, or infinite phase limit falls back to 90 seconds
for that phase, keeping unattended updates bounded. Timing out the `systemctl`
client does **not** cancel the manager's transaction. Custom multi-command stop
chains or `EXTEND_TIMEOUT_USEC` can still outlast this estimate; a real timeout
remains an incomplete restart, and successful commands still require the existing
service-health and fleet-version verification. Raw numeric `*USec` values are
microseconds, while formatted values use systemd's fixed units, including days,
weeks, months and years. The combined timeout is capped below the native signed
32-bit millisecond poll limit (with rounding headroom), so exceptionally long
unit limits cannot overflow subprocess polling. Zero/unknown/infinite phase
limits use the bounded fallback. This does not change active-turn drain settings.

## Nous free tier sign-in

Sign-in completion is one function, `settle_after_upgrade`, called by every caller that persists an
account over a free-tier identity (CLI `upgrade_guest`, the desktop poller): it moves a config on the
welcome route to the account's host and the tier's recommended default
(`models.recommended_nous_default_model`, shared with `GET /api/model/recommended-default`).

The shared flow, states, and copy live in `anon_sign_in.py`; CLI rendering lives in
`anon_sign_in_cli.py`. `anon_auth.py` keeps identity, promotion polling, and settlement, and
re-exports the existing sign-in API. The flow resolves identity and persistence collaborators
through `anon_auth` at call time to preserve module-attribute monkeypatch seams.

The sign-in itself is one composition: `anon_auth.run_sign_in()` yields `SignInState`s (`Code`,
`Waiting`, `Completed`, `Declined`, `Superseded`, `TimedOut`, `Retired`, `Failed`,
`AlreadySignedIn`, `Unavailable`). It reads the current state itself, holds one absolute deadline
across both waits, persists only after a completed promotion **and** a token grant, runs
`settle_after_upgrade` exactly once per completion, and never lets a persist or settle failure
escape as an exception — it becomes `Failed`. Every state carries its own `.copy` (the chat form,
which never contains a raw exception, a URL or a `hermes` verb) and `.copy_terminal`, so no caller
maps a reason to a string. `cancelled()` stops an attempt; `cancel_wins_after_promotion` decides
what happens when the server had already completed the transfer — the desktop keeps `True` (a
DELETE means "not on this machine"), the gateway passes `False` (a supersede must not discard a
transfer the user actually approved). `scope` is entered only around the precondition and persist
blocks, never across a `yield` or a network wait, because `run_in_executor` does not carry
contextvars. `upgrade_guest` (`hermes auth upgrade`), the CLI `/login` handler and the desktop
promotion poller are renderers over it; a surface that needs the cancel check and the save to be
atomic passes `persist_guard`. The desktop's plain "connect another Nous account" device-code login
is a separate path (`_nous_plain_poller`) and must stay one.

## Process identity: never infer it from argv substrings

The bug class behind ~10 fleet-update issues (#90778, #87594, #78089, #76129, #91964, ...):
classifying a process by `"serve" in cmdline` or similar. `kanban --preserve-cache` contains
"serve"; a flag VALUE can equal a subcommand (`-m dashboard serve`); truncated cmdlines hide the real
subcommand. Rules:

- Use the canonical matchers: `gateway.status.looks_like_gateway_command_line` (gateway run),
  `hermes_cli.update_cmd._hermes_holder_subcommand` (top-level subcommand of any Hermes argv). Never
  hand-roll token scans.
- Flag sets must be DERIVED from the parser (`_holder_value_flags()` introspects
  `build_top_level_parser()`), never hand-written lists — they drift.
- Never blanket-exclude ancestors from process scans: when `/update` runs as the gateway's child, a
  gateway ancestor must stay visible to the pause machinery (#87594). Exclude interactive ancestry,
  carve out gateway-shaped ancestors.
- Match on FULL cmdlines; truncate only at display time (#78089).
- Before adding any new scan heuristic, read #92091 — the gateway control socket replaces scans as
  the primary coordination mechanism; scans are the fallback layer for old/crashed processes.

## Skin engine — what skins customize

| Element | Skin key | Used by |
|---|---|---|
| Banner panel border / title / section headers / dim / body | `colors.banner_border`, `banner_title`, `banner_accent`, `banner_dim`, `banner_text` | `banner.py` |
| Response box border | `colors.response_border` | `cli.py` |
| Spinner faces (waiting / thinking) | `spinner.waiting_faces`, `spinner.thinking_faces` | `display.py` |
| Spinner verbs / wings (optional) | `spinner.thinking_verbs`, `spinner.wings` | `display.py` |
| Tool output prefix / per-tool emojis | `tool_prefix`, `tool_emojis` | `display.py` → `get_tool_emoji()` |
| Agent name / welcome / response label / prompt symbol | `branding.agent_name`, `welcome`, `response_label`, `prompt_symbol` | `banner.py`, `cli.py` |

Built-in skins (`_BUILTIN_SKINS` in `hermes_cli/skin_engine.py`): `default` (classic gold/kawaii),
`ares` (crimson/bronze with custom spinner wings), `mono` (grayscale), `slate` (cool blue). Add a
built-in as a dict entry `{"name", "description", "colors", "spinner", "branding", "tool_prefix"}`.
User skins are `~/.hermes/skins/<name>.yaml` with the same keys, activated with `/skin <name>` or
`display.skin: <name>`; the full YAML template is in the
[Skins & Themes](../user-guide/features/skins.md) user guide.

## Profiles: multi-instance support

Hermes supports profiles — fully isolated instances, each with its own `HERMES_HOME` (config, API
keys, memory, sessions, skills, gateway). For single-profile commands (`hermes -p x <cmd>`),
`_apply_profile_override()` in `hermes_cli/main.py` sets `HERMES_HOME` before any module imports, so
every `get_hermes_home()` reference scopes to the active profile. The multiplex gateway and the
Desktop/dashboard `serve` backend serve several profiles from one process instead: the active
profile is a contextvar override bound per activity, `os.environ["HERMES_HOME"]` stays the launch
profile's, and a module-level constant derived from the home freezes to that launch profile (see
[Gateway Internals § Multiplexed profiles](./gateway-internals.md#multiplexed-profiles)). Profile
operations are HOME-anchored (`_get_profiles_root()` returns
`Path.home() / ".hermes" / "profiles"`, not `get_hermes_home() / "profiles"`) so
`hermes -p coder profile list` sees all profiles regardless of which one is active — intentional.
Profile-safe coding rules are in the root `AGENTS.md`; multiplex secret-scope rules in
`gateway/AGENTS.md`.

### Profile and supervisor lifecycle contracts

`_apply_profile_override()` in `hermes_cli/main.py` sets `HERMES_HOME` before any module import for
single-profile commands (`hermes -p x <cmd>`), so there `get_hermes_home()` scopes to the active
profile. The multiplex gateway and the Desktop/dashboard `serve` backend instead bind the active
profile per activity via a contextvar override while `os.environ["HERMES_HOME"]` keeps the launch
profile — a module constant or import-time read there freezes to the launch profile (rules in
root). Profiles are independent
islands by design — no live config inheritance; `--clone` copies at creation, minus messaging
channels (`profile_channels.py`: ownership-based inventory evaluated in the SOURCE's plugin scope —
adapter-declared keys + canonical/alias prefixes + `GATEWAY_ALLOW*`/`GATEWAY_RELAY_*`; prefixes shared
with tools (`TWILIO_`/`EMAIL_`, plus a plugin platform's `shared_env_prefixes`) are stripped only when the source runs that adapter; never a hand
list; a platform that left core keeps its ownership from its `LEFT_CORE` row while the plugin is absent). `--clone-channels` opts in and its live-multiplexer refusal lives in `create_profile` (CLI, REST
and TUI all go through it). Clones are built in `profiles/.<name>.staging-<pid>` (hidden → invisible to
`_iter_named_profile_dirs` and the hot-serve rescan) and published by one `os.rename` after the strip;
symlinked `.env`/`config.yaml` are materialized first so a clone never writes through to its source. Multiplex
(`gateway.multiplex_profiles`) secret-scope rules: `gateway/AGENTS.md`. The served set is
`profiles.py::profiles_to_serve(multiplex=True)` = default + every live dir under `profiles/` — live =
carries an identity marker (`hermes_constants.named_profile_has_identity`: `config.yaml`/`.env`/`SOUL.md`/
`profile.yaml`/`auth.json`/`state.db`) and is not tombstoned. A marker-less dir (cron/log side-effect
shell, stray infrastructure dir) is never listed, served, ticked, `.env`-backfilled or resolvable via `-p`
(#95188, #99392); `profile create` replaces it only when it is also tombstoned (a live marker-less dir may hold user
files — fail closed, never rmtree). A dangling symlinked marker still counts as identity (`is_symlink()`).
`tools/bot_mode_probe._roster` (Bot Mode teammate roster, `bot_relay.deliver` target check) applies the same
predicate. There is no allowlist (`gateway.multiplex_profile_allowlist` was retired in config v43).
Enumeration is a pure read: never `mkdir` a profile home from a served path (`SessionDB`, logging,
cron all go through `mkdir_under_hermes_home` / `_ensure_cron_dir`, which refuse a deleted or
missing named profile, #94590). Process-global per-profile slots (MCP discovery in `mcp_startup.py`,
tool registry overlays) key on `hermes_constants.hermes_home_key()`, never a single flag.
`gateway.multiplex_profiles` defaults to **on**, but `GatewayConfig` keeps an unset flag `None` and
`gateway_multiplex_mode.resolve_multiplex_mode` settles it once per boot (called from
`load_gateway_config_for_runner`): default profile, >= 2 profiles, no standalone secondary gateway,
no preflight blocker, migratable host → `True`; else `False` + a logged reason. Explicit values pass
through. CLI/dashboard readers use `default_gateway_multiplexes` (live `served_profiles` record, then
the explicit flag) — never the merged default, which would guess a verdict only the gateway makes.
Migration from per-profile gateways: `hermes_cli/gateway_migrate.py` (`hermes gateway migrate
--multiplex`, the only mode — `--standalone` is deleted and a per-profile fleet is not a supported
target; table-driven `_PREFLIGHT_CHECKS`; manifest `<default>/gateway_migration.json` = UNFINISHED,
a re-run resumes from it; a named profile's `gateway install|start|run` refuse without `--force` via
`gateway.py::_named_profile_refused_under_multiplexer`, dashboard twin
`web_server_gateway.py::multiplexed_profile_refusal`);
`update_cmd_fleet_verify._verify_fleet_after_update` calls `maybe_auto_migrate_after_update` on the success
path only; `gateway_migrate_guards.py` holds the auto-path-only refusals (table `_AUTO_MIGRATION_GUARDS`:
other service domain / UNIX user / HERMES_HOME outside `profiles/` — notices for the explicit command,
blockers for the hook) and the `gateway.auto_multiplex_migration` opt-out (#109954). Blockers reuse `GatewayRunner._adapter_credential_fingerprint` and `platform_binds_port`;
"has a `/p/<profile>/` ingress" is the adapter class attribute `serves_profile_prefix` — set it on a
new HTTP-inbound adapter when it answers the prefix, never extend a list here.
`hermes gateway restart` for a gateway Hermes did not install (custom launchd agent / unit running
`gateway run --external-supervisor`): `gateway_supervised_restart.py` — the gateway's SELF-declared
supervisor (control-socket `identify` answering anything but `manual`, OR the argv marker) decides; hand back via SIGUSR1 and wait
for a fresh supervised PID, never stop + foreground `run_gateway` (that stamps the CLI's PID and wedges
every KeepAlive respawn, #110637).

Service installs are a matrix, not a unit file: `gateway.py::generate_systemd_unit(system=,
run_as_user=)` (systemd unit generation / `systemd_unit_is_current` / `refresh_systemd_unit_if_needed` are PM-aware facade owners; user unit AND `--system` unit with `User=`; an unresolvable `User=` is a blocker,
never a dir-owner fallback), `gateway_launchd.py::generate_launchd_plist` (`gui/<uid>` then `user/<uid>` domains, never a
`~/Library/LaunchAgents` glob; the whole launchd backend — plist refresh, `launchctl` bootstrap/kickstart,
`launchd_start/stop/restart/status`, detached-process degrade — lives in that sibling, with the domain cache
`_resolved_launchd_domain` staying a facade global), Windows Scheduled Task and the Desktop-spawned backend all carry the
profile's `HERMES_HOME` (and `HOME` for the service user) explicitly — a supervisor starts with an
empty environment, so the env override that makes `-p` work interactively does not exist there. A
change to install/restart/status regenerates and diffs every kind; both user and system units are
recorded when both exist. Process liveness is `(pid, start_time)` or the canonical matchers
(`gateway.status.live_gateway_pid_for_home`), never bare PID existence.
