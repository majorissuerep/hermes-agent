---
sidebar_position: 2
title: "Installation"
description: "Install, update and repair the terminal-only encrypted Hermes fork on Linux or macOS"
---

# Installation

This is the unofficial [majorissuerep fork](https://github.com/majorissuerep/hermes-agent),
not a Nous Research release. It provides a local Ink TUI and headless terminal agents
on Linux and macOS. Desktop, messaging gateways and remote-management servers are not included.
Report fork problems to the [fork issue tracker](https://github.com/majorissuerep/hermes-agent/issues).

## Install

From a terminal:

```sh
curl -fsSL https://raw.githubusercontent.com/majorissuerep/hermes-agent/main/scripts/install.sh | bash
```

Open a new shell, then run:

```sh
hermes secure-vault status
hermes setup
hermes
```

The installer uses the fork's `main`, provisions PM-managed Python and Node,
prepares dependencies and the TUI, and installs the `hermes` launcher. Git, curl,
tar and SHA-256 utilities must be available. See [platform support](./platform-support.md)
and [package management](../reference/package-management.md) for supported runtimes.
Native Windows is not supported; use Linux through WSL instead.

### Initialize encrypted state {#initialize-encrypted-state}

If `secure-vault status` reports no vault, initialize one before configuring providers:

```sh
hermes secure-vault migrate
hermes secure-vault status
```

Choose a passphrase in the terminal prompt, never in chat, argv or an environment
variable. Migration creates a pre-encryption backup. Verify the installation before
securely removing that **plaintext backup**. Losing every unlock credential means
losing access to encrypted data.

Container images keep their code immutable. When the data home's parent is
read-only, migration uses the provisioned owner-only sibling directory
`<HERMES_HOME>-migration-backups`; the reported backup remains outside the encrypted home.

Unlock once when starting a session. Local self-management commands and delegated
terminal agents reuse temporary, profile-scoped session authority without another
prompt. Closing the owner revokes it. An independent new session still unlocks.
For explicitly unattended local commands, see the private-key options in
`hermes secure-vault keygen --help` and `hermes secure-vault add-key --help`;
`HERMES_VAULT_PRIVATE_KEY` is a private-key **file path**, not a passphrase.

### Installer options

```sh
installer=$(mktemp)
curl -fsSL https://raw.githubusercontent.com/majorissuerep/hermes-agent/main/scripts/install.sh -o "$installer"
bash "$installer" --help
rm "$installer"
```

- `--dir` selects the source checkout; `--hermes-home` selects user data independently.
- `--non-interactive` skips interactive setup. Configure and unlock afterward.
- `--skip-browser` and `--skip-computer-use` omit optional browser/GUI-control downloads.
  These choices persist across updates. Add them later with `hermes pm install agent-browser`
  or `hermes pm install cua-driver`.
- `--verbose` streams step output. Otherwise failures show their log and failing stage.

The default source checkout is under the Hermes home, and the launcher is under
`~/.local/bin`. `HERMES_HOME` selects a different data home. PM owns its tool store
and environment generations; do not repair dependencies with raw pip/uv commands.
Do not delete the user-data directory to repair an application installation.

## Switch an existing installation {#switch-existing-installation}

Back up the existing home, save work, and close its Hermes sessions before takeover:

```sh
curl -fsSL https://raw.githubusercontent.com/majorissuerep/hermes-agent/main/scripts/takeover.sh | bash
```

The takeover retains the previous checkout, installs the fork and migrates existing
state with a backup. Verify provider settings, a real conversation, and retained
history before deleting old code or plaintext migration backups. Unrelated local
files and existing credentials must not be discarded. A provider removed from this
fork needs an explicit [supported connection](../integrations/providers.md).

For a different layout, install with explicit `--dir` and `--hermes-home` paths,
then run `hermes secure-vault migrate` if that data home has no vault.

## Update and repair

```sh
hermes update --check
hermes update --backup
hermes doctor
hermes pm repair
```

A standalone encrypted update unlocks once; an update called from an unlocked local
session reuses its authority. Update completion prepares the native runtime and TUI,
without starting services. Restart your terminal session after an update.

If tracked code or the managed interpreter cannot start, rerun the source installer
against the same paths. Keep backups and unrelated local changes. If encrypted state
was overwritten or startup reports mangled ciphertext:

```sh
hermes secure-vault repair
```

Repair preserves damaged input and reports unrecoverable items; it must not silently
replace them with empty settings. See [encrypted state and recovery](../user-guide/encrypted-state.md)
for verified backup/restore and offline plaintext export. Stop other local sessions
before restoring state. Never overwrite encrypted configuration with a text editor.

## Choose a provider and verify

```sh
hermes model
hermes tools
hermes
```

The supported connections are ChatGPT/Codex, Anthropic, OpenAI API, LiteLLM,
custom OpenAI/Anthropic, Z.AI, Grok and Vercel. See [AI providers](../integrations/providers.md).
A successful login is not proof of a working model: send a message, exercise a tool,
then resume the conversation. See the [quickstart](./quickstart.md) and [TUI guide](../user-guide/tui.md).
