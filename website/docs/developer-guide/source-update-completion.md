# Source update completion ownership

## Phase seam

The command process owns admission, the update lock and output lifetime,
all-profile snapshots, Git selection/stash/restore, syntax/HEAD guards, and ZIP
staging and swap. It imports the completion transport before replacing code.
Git, already-current retry and ZIP completion all send one versioned JSON
request to `update_completion.py` from the selected tree. Cached application
modules are not evicted or reloaded in the old interpreter.

The request contains source/home paths, interaction mode, the prior version,
active and sibling snapshot identifiers, and receipt identity/data. It carries
no callables, pickles, passwords or keys. Historical request fields are admitted
only for old-updater takeover; they do not revive removed Desktop or gateway
services. Child output remains visible through the parent's redacting output
stream.

## New-code owner

A stdlib-only entrypoint starts with `-I -S`: no old site-packages or executable
`.pth` files initialize. A private bytecode-cache prefix fences stale caches,
and the explicit import path points at the selected checkout. The new PM
interface prepares the recorded dependency union. Only the selected Python,
after leasing and activating its environment generation, imports application
completion code. The same receipt identity crosses every preparation boundary.

For encrypted homes, `hermes_cli/update_vault.py` carries unlock authority over a
private inherited pipe. The stdlib preparer forwards PM config and receipt
operations to the unlocked parent without importing cryptography or activating
the old dependency graph. The selected application process adopts keys only
after dependency activation. Neither keys nor passwords enter JSON requests,
environment variables or PM workers. Interrupted completion uses the same
transport. PM unlocks its owner before starting an install-lock-holding worker.
Receipt acknowledgement decrypts the correlated receipt; on-disk receipts stay
encrypted, including preparation failures.

Completion publishes launchers, builds the native TUI, invalidates caches and
maintains profile config/state/skills. It does not start remote services.

## Commit point and owed work

Before the source commit point, refusal/concurrency is exit 2; a failure with no
committed replacement, or a syntax rollback, is exit 1. After the tree moved,
independent maintenance failures are visible warnings and receipt follow-ups
(`{step, reason}`), not a claim that code rolled back. The source-completion
obligation remains armed until the owed work succeeds. The next launch or
`hermes update`, including the already-current path, retries completion.

A dependency-preparation failure records `dependencies`; a prepared child dying
without a correlated result records `completion`. A post-commit Ctrl-C records
`interrupted` with exit 130 and explicitly says the new code is in place.
A receipt-store failure warns and returns the correlated terminal record in
memory; durable retention cannot be claimed if storage refused the write.

The receipt is written as `running` at admission and refreshed at stage
boundaries. A later update marks a dead owner's run interrupted and names its
last stage. Receipts and the update-log tee resolve to the root home, never a
sticky profile. Per-profile maintenance errors remain visible; an error escaping
that step becomes a `profile_sync` follow-up and keeps completion pending.

## Parent lifecycle

A child cannot succeed merely by exiting zero: it must return the matching
terminal acknowledgement. The parent retains its receipt until that response,
propagates a genuine nonzero result and normalizes signal exits to 128+signal.
Completion owns its process group; cancellation kills that group before the
update lock is released. Do not automatically roll code back after maintenance
began: state snapshots are file-loss recovery, not schema rollback.

## Damaged recovery code

Launch-time repair requires both the checkout lock and child-custody runner.
If neither verified working-tree code nor a verified recovery copy is available,
launch refuses with `Cannot safely repair` and keeps the interrupted marker.
It never writes without exclusion. Wait for a live update to finish.

Before a tree move, committed bootstrap, early-recovery, lock and custody files
are published under `<git dir>/hermes-update-recovery/<pre>/`, keyed by the full
starting commit and verified Git blob IDs. Files and directory are fsynced before
publication. If the new tree is torn, the minted launcher uses only that verified
copy, or rebuilds it from the recorded commit's Git objects with replacement
objects disabled. The same restore claim and checkout lock apply.

This fallback belongs to minted launchers, not arbitrary `python -m` imports.
An older launcher has no fallback on its first update onto this code. A starting
commit lacking the required recovery/lock/custody files is not admitted as a
recovery authority. Preserve that boundary in interrupted-swap tests.
