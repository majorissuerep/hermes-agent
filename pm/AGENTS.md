# pm/ — dependencies and Hermes environments

Applies on top of the root `AGENTS.md` (which carries the short form of the pinning rule).

## Dependency pinning policy

All dependencies carry upper bounds (litellm compromise #2796/#2810; Mini Shai-Hulud worm,
May 2026). PyPI: `>=floor,<next_major` (`"httpx>=0.28.1,<1"`); pre-1.0: `<0.(minor+2)`
(`>=0.29,<0.32`). Git URLs: 40-char commit SHA. GitHub Actions: SHA + `# vN` comment. CI-only
Python requirements: `==exact`. A bare `>=X.Y.Z` is rejected by CI and reviewers.
After changing `pyproject.toml`, run `hermes pm lock`, re-source `./activate`, and commit
`pyproject.toml` with `uv.lock`. Reference: #2810 (bounds), #9801 (SHA pinning + audit CI).

PM owns Hermes Python dependency changes. Use `pm.sync_venv(['extra'], explicit=True)`
for declared runtime extras, `hermes pm install` for setup/sync, and `hermes pm repair`
for damaged dependencies. Do not mutate Hermes environments with raw pip or uv.
Use `pm.build_environment` for fresh build outputs and `pm.ensure_environment` for
isolated tool environments. Callers receive an interpreter or tool path, not uv.
Nix's declarative uv2nix builds and unrelated user projects remain independently owned.

The `[tool.uv] exclude-newer = "14 days"` release-age check covers **Hermes's own dependencies
only**, exact pins included, and it runs when Hermes LOCKS
(`hermes pm lock`, CI's lock check), never on users' machines: every generated workspace drops the
cutoff (`pm/workspace.py::_without_release_age`), so a mirror without upload times cannot break an
install (#135589). Plugin resolution is unchanged by this: core's exact pins stay put, and a plugin
may move a transitive core package within core's declared ranges
(`tests/pm/test_workspace_build_inputs.py::test_plugin_can_move_compatible_transitive_but_not_exact_requirement`).
Plugin `python_dependencies` follow the plugin's own policy. Teknium's rulings: "plugins dont have to abide by our 14 day rule … Only hermes'
dependencies themselves have to"; dates are checked by us, not users.

The owner approved one temporary exception to preserve the synced native ABI:
`nemo-relay==0.10.0`, with an upload cutoff ending immediately after that release.
Remove its timestamp override after 2026-10-23 UTC. No floating or blanket exemptions
are authorized; every other dependency retains the fourteen-day rule. Generated
installation workspaces strip both the global cutoff and this package-specific
table, since release-age verification belongs to locking, not user installation.
