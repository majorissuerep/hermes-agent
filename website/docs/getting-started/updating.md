---
sidebar_position: 3
title: "Updating & recovery"
description: "Maintain the encrypted terminal-only Hermes fork on Linux and macOS."
---

# Updating & recovery

This fork ships a local terminal UI and finite terminal agents. It does not ship
Desktop, messaging gateways, a dashboard or remote-management services.

## Updating

For a managed source installation:

```sh
hermes update --check
hermes update --backup
```

The source channel is this fork's `main`, not upstream release tags. Upstream
changes are available only after integration into the fork. To move an existing
upstream installation, use [installation takeover](./installation.md#switch-existing-installation).

A standalone encrypted update unlocks once. An update invoked inside an unlocked
Hermes session reuses that session's home-scoped authority, including dependency
preparation and verification children. Never put a passphrase in chat, argv or
an environment variable. Explicitly unattended commands need an authorized
private-key file; see [encrypted-state setup](./installation.md#initialize-encrypted-state).

| Installation | Update method |
|---|---|
| Managed source checkout | `hermes update` |
| Docker image | Pull the selected image and recreate the container with the same data mount |
| Nix | Update the flake/profile and rebuild |

`hermes update` does not overwrite image- or package-owned application files.
PM manages dependencies; `hermes pm update` changes dependency pins for maintainers,
not the installed application version.

### What happens during an update

1. Snapshot selected state for the active home and sibling profiles. The default
   `updates.pre_update_backup` is `quick`; `full` makes a complete backup, and
   `off` or `--no-backup` disables it. Snapshot failure is a visible warning, not
   proof that a recovery copy exists.
2. Save local source changes, select the new source tree and validate startup
   syntax. Invalid replacement code is rolled back before maintenance proceeds.
3. Prepare and validate PM-managed dependencies, publish the launcher and rebuild
   the TUI when its real inputs changed.
4. Migrate configuration, synchronize bundled skills and finalize the encrypted
   update receipt. No gateway or remote service is started.

A failed post-commit maintenance step is reported as a follow-up, with the new
code already in place. An interrupted update retains its recovery obligation;
the next launch or `hermes update` retries it. Read the warning and receipt rather
than treating exit zero alone as proof that every follow-up completed.

### Local source changes

Interactive updates preserve source edits in an autostash and offer restoration.
For unattended updates, `updates.non_interactive_local_changes: stash` is the
safe default: save and restore local work. `discard` is an explicit opt-in to
throw those edits away. Conflicted restoration leaves a recoverable Git stash.

`hermes update --keep-stash` keeps saved edits parked instead of reapplying them.
Use `git stash list` and the printed recovery command before deleting a stash.
User configuration, credentials, databases and other ignored runtime state are
not source edits and must not be swept into an autostash.

For a checkout parked on a feature branch, `updates.parked_branch_strategy`
defaults to switching a clean checkout back to the update target while retaining
the old branch. `update_in_place` instead merges the target into your custom
branch. A dirty parked branch is not overwritten; follow the printed recovery
instructions. `--switch-branch` selects the switch behavior for one update.

### Preview and validation

`hermes update --check` compares the checkout without applying code or installing
dependencies. It may fetch Git metadata; it is not a zero-filesystem-write promise.
`hermes update --plan` reports the owning installation and update method.

```sh
hermes --version
hermes doctor
hermes config check
hermes secure-vault status
```

Receipts are under the root home's `logs/update_receipts/`; logs and receipts stay
encrypted. Use Hermes commands or unlocked managed readers, not a text editor.
Restart your terminal session after updating, then verify the selected provider,
a real conversation and retained history. Existing sessions retain their cached
prompt; configuration edits normally take effect in a new session.

Disable passive notices with `hermes config set updates.check false`. Explicit
`hermes update --check` and `hermes update` continue to work.

## Repair and restore

```sh
hermes pm repair
hermes secure-vault repair
```

PM repairs managed tools and environments. Vault repair preserves damaged inputs
and reports unrecoverable items; it must not silently replace them with empty
settings. If source files or the interpreter cannot start, rerun the source
installer against the same checkout and home. Do not delete user state to repair
application code.

For interrupted checkout recovery, wait for any live update to finish. A refusal
such as `Cannot safely repair` means Hermes could not establish exclusive,
verified recovery ownership; retain the marker and damaged tree for diagnosis.

Close other sessions before restoring state. See [encrypted state and recovery](../user-guide/encrypted-state.md)
for snapshots, retained-backup import and explicit bulk plaintext export. Never
use raw `git reset --hard`, raw pip/uv or plaintext config writes as a substitute
for the owning update/repair workflow.

## Uninstalling

Inspect the removal plan first:

```sh
hermes uninstall --dry-run
hermes uninstall
```

The normal uninstall can retain data for reinstall. `--full` also removes user
data; `--data` removes only the active home's data. Export and verify any state
that must survive before choosing a destructive mode. Image- and package-managed
installations must remove application code through their owning package manager.
