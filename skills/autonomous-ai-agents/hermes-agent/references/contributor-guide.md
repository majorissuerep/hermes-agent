# Development in this fork

Read the checkout's root and area `AGENTS.md` before edits. The runtime is terminal-only:
`agent/` owns the model loop, `hermes_cli/` owns local commands/update/config,
`tui_runtime/` is the private stdio Python runtime, and `ui-tui/` is the Ink frontend.
`hermes_state/` owns SQLCipher history, `hermes_security/` vault/key capabilities,
`tools/` native execution, and `pm/` managed dependencies.

Preserve two invariants: cached per-conversation prompt prefixes are append-only
except explicit compression; new capability belongs at the edge, not in core tools.
Do not reintroduce Desktop, messaging gateways, service supervisors, HTTP management
routes or remote session ownership under new names.

Use isolated `HERMES_HOME` / `HERMES_RUNTIME_DIR` before activation. Dependencies are
PM-owned, bounded and locked; never mutate a live environment with raw pip/uv.
Run `python scripts/check` and the canonical `scripts/run_tests.sh` runner.

Prove bugs on the baseline, including intent/history, and retain one or two actual
behavior regressions red on the base. Security/config/I/O changes need real imports
against temporary encrypted state. User-facing model/launcher changes also need a
public PTY path, not only an internal RPC test. Cold lifecycle/live-provider evidence
comes from disposable containers/VMs with exact source identity and no personal home.

Run OS-specific tests on that OS; never fake `sys.platform`. Record skipped, failed,
retried and unexecuted coverage separately. Missing credentials are not green live
coverage. See the repository's reliability guide and `scripts/e2e/README.md`.
