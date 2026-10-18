---
title: "TUI from Worktrees"
description: "Build the local Ink TUI from an isolated source checkout"
---

# TUI from worktrees

A worktree owns its source and locked dependency selection. Use a build-owned
Hermes home/runtime and the PM-provisioned Node/npm tools; do not borrow mutable
`node_modules` from a checkout with a different lockfile.

From the worktree root:

```sh
node scripts/build/node-deps.mjs --source "$PWD" --workspace ui-tui --workspace apps/shared
node scripts/build/tui.mjs --source "$PWD" --out "$PWD/.build/tui"
```

The dependency provider validates the workspace union and root lock. `--reuse`
accepts a matching completed dependency receipt, not an arbitrary installed tree.
Compilation still runs. Keep application qualification separate from build-only
checks: launch the exact candidate in a disposable native environment with isolated
state, rather than opening the workstation's installed Hermes.
