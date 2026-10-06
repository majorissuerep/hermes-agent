---
sidebar_position: 2
title: "Installation"
description: "Install Hermes Agent with desktop bundles, source installers, Docker, Nix, or the Termux APT package"
---

# Installation

:::warning Unofficial fork
This repository is an **unofficial fork** of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent), maintained by [@majorissuerep](https://github.com/majorissuerep). It is not built, supported, or endorsed by Nous Research, and it diverges from upstream (encrypted state at rest, different security defaults, fork-only integrations). The install commands on this page install **this fork**. Review the code before installing, and report problems to the [fork's issue tracker](https://github.com/majorissuerep/hermes-agent/issues) — never upstream. For the original product, use [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).
:::

Install the fork, initialize its encrypted state, and choose a model provider.

:::tip Platform Support
For the full platform support matrix (which OSes, distribution methods, and
platform-gated features are supported), see **[Platform Support](./platform-support.md)**.
:::

## Quick Install

These installers clone `majorissuerep/hermes-agent` and track its `main` branch. If you already run upstream Hermes, use the [migration instructions below](#switch-existing-installation).

#### Linux / macOS / WSL2
```bash
curl -fsSL https://raw.githubusercontent.com/majorissuerep/hermes-agent/main/scripts/install.sh | bash
```

#### Windows (native)

Run in powershell:
```powershell
iex (irm https://raw.githubusercontent.com/majorissuerep/hermes-agent/main/scripts/install.ps1)
```

### Initialize encrypted state {#initialize-encrypted-state}

Open a new terminal after installation and check whether setup already created the vault:

```bash
hermes secure-vault status
```

If no vault exists, initialize it from an interactive terminal:

```bash
hermes secure-vault migrate
```

Review the migration plan and choose a master password. The command backs up the home before encrypting its state. Keep your password or an authorized private key safe; losing every unlock credential means losing access to the data. After verifying the migrated installation, securely remove the **plaintext pre-migration backup** printed by the command.

Then verify the vault and configure the provider:

```bash
hermes secure-vault status
hermes setup
```

State-accessing commands prompt for the master password. For unattended services, authorize a private key with `hermes secure-vault keygen` and `hermes secure-vault add-key --public-key <file>`, then give the service `HERMES_VAULT_PRIVATE_KEY=/path/to/private-key`. This variable contains a **file path**, never the master password. Use `--help` on those commands for their key-file options.

If startup reports that `.env` **holds mangled ciphertext** or fails vault
authentication, run:

```bash
hermes secure-vault repair
```

This command remains available when automatic update completion fails. It reuses
an existing unlock or `HERMES_VAULT_PRIVATE_KEY` key file, otherwise prompting for
the master password, and attempts to restore the damaged `.env` from a
pre-migration backup, preserving the damaged contents as encrypted `.env.clobbered`.
If no usable backup exists, repair reports the file as unrecoverable; preserve the
existing files and recover or re-enter the credentials before resuming setup.

### Switch an existing installation to the fork {#switch-existing-installation}

On Linux, macOS, or WSL2, run the takeover script from a terminal after saving ongoing work and closing Hermes Desktop:

```bash
curl -fsSL https://raw.githubusercontent.com/majorissuerep/hermes-agent/main/scripts/takeover.sh | bash
```

The script stops running Hermes processes, keeps the previous source checkout, installs the fork's `main` branch, and migrates the active home with a backup and verification. It uses the same PM-managed Python, dependencies, app builds, and launcher as a fresh source installation. The source lives under `$HERMES_HOME/hermes-agent` (defaulting to `~/.hermes/hermes-agent`), and the launcher under `~/.local/bin`. For another layout or native Windows, keep a backup, stop the existing Hermes processes, install the fork for the same data home, and run `hermes secure-vault migrate` if that home has no vault.

With a terminal available, takeover asks for a master password. Without a terminal it creates a key-only vault and prints the private-key path; retain that file and provide its path through `HERMES_VAULT_PRIVATE_KEY` when starting Hermes. Check `hermes secure-vault status` and a real chat before removing the saved source checkout or plaintext migration backup.

### Hermes Desktop

The downloads at [hermes-agent.nousresearch.com](https://hermes-agent.nousresearch.com/) install upstream Hermes. For this fork, install the CLI and initialize the vault above, then build and launch Desktop from the installed checkout:

```bash
hermes desktop
```

### Android / Termux

The installer provisions dependencies (Python, Node.js, ripgrep, ffmpeg), clones the fork, creates the virtual environment, sets up the global `hermes` command, and offers provider configuration. Complete the vault setup above before starting a chat or service.

For Android, see [Termux](./termux.md); the signed APT packages are upstream builds and do not include this fork.

### What the source installer does

The scripts clone the source, bootstrap uv, and delegate dependency preparation
to PM. PM provides pinned Python, Node.js, npm, ripgrep, and FFmpeg. The source
installation selects the `all` Python extra, not every optional extra.
PM also installs the browser and computer-use tools by default: `agent-browser`
and its pinned Chromium, and `cua-driver` (the computer-use driver, on macOS,
Windows and glibc Linux). If a download fails, the install still completes and
prints the command to retry. The default browser driver (browser-harness, the
engine of the Browser Use CLI) is a regular Python dependency, so every install,
the Desktop app included, already has it.
Other optional tools use their feature-specific installation paths.

To leave the browser tools out, pass `--skip-browser` on POSIX or `-SkipBrowser`
on Windows; for the computer-use driver, `--skip-computer-use` /
`-SkipComputerUse`. Hermes remembers these choices: later installs and
`hermes update` do not add them back. Run `hermes pm install agent-browser` or
`hermes pm install cua-driver` to install them and undo the choice.

The scripts create a launcher and prepare the data directory. Interactive runs
also invoke setup and gateway configuration. `--non-interactive` on POSIX, or
`-NonInteractive` on Windows, skips stages that need input. The optional
`--include-desktop` / `-IncludeDesktop` stage builds the desktop from source.

On a terminal the scripts show one status line per step and write the output
of git, uv and the builds to `logs/install.log` under the Hermes data
directory; a failed step prints its last lines and the log path. CI (`CI` or
`GITHUB_ACTIONS` set), redirected output, `--verbose` / `-Verbose` or
`HERMES_INSTALL_VERBOSE=1` stream everything instead.

#### Install layout

| Method | Code | CLI entry point | Default user data |
|---|---|---|---|
| POSIX source script | `~/.hermes/hermes-agent/` | `~/.local/bin/hermes` wrapper | `~/.hermes/` |
| Windows source script | `%LOCALAPPDATA%\hermes\hermes-agent\` | `%LOCALAPPDATA%\hermes\bin\` | `%LOCALAPPDATA%\hermes\` |
| Desktop bundle | Inside the installed app package | Packaged launchers; Windows execution aliases | Platform default Hermes data directory |
| Docker | `/opt/hermes/` | Image entrypoint and `hermes` shim | Mounted `/opt/data/` |
| Termux APT | `$PREFIX/lib/hermes-agent/` | Symlinks in `$PREFIX/bin/` | `~/.hermes/` |

`HERMES_HOME` selects user data. The POSIX script's `--dir` selects its source
checkout independently. Windows provides `-HermesHome` and `-InstallDir`.
Running the POSIX script as root does not select an automatic FHS layout:
it uses root's home unless you provide an explicit source path.

PM's tool store and per-install Python generations have separate lifetimes.
See [Package management](../reference/package-management.md) for their locations.
Do not remove the data root to repair an application installation.

### After Installation

Reload your shell and start chatting:

```bash
source ~/.bashrc   # or: source ~/.zshrc
hermes             # Start chatting!
```

To reconfigure individual settings later, use the dedicated commands:

```bash
hermes model          # Choose your LLM provider and model
hermes tools          # Configure which tools are enabled
hermes gateway setup  # Set up messaging platforms
hermes config set     # Set individual config values
hermes config get     # Inspect individual config values
hermes setup          # Or run the full setup wizard to configure everything at once
```

:::tip Fastest path: Nous Portal
One subscription covers 300+ models plus the [Tool Gateway](../user-guide/features/tool-gateway.md) (web search, image generation, TTS, cloud browser). Skip the per-tool key juggling:

```bash
hermes setup --portal
```

That logs you in, sets Nous as your provider, and turns on the Tool Gateway in one command.
:::

:::tip Already running Hermes on another machine?
You don't need to rebuild your setup from scratch. Restore a full backup with `hermes import` (see [Exporting Hermes to another machine](../reference/faq.md#exporting-hermes-to-another-machine)), or bring over a single agent with `hermes profile import` (see [Moving a single profile to another machine](../reference/faq.md#moving-a-single-profile-to-another-machine)). Note that a profile export excludes credentials by design, so an export alone is not a full backup — [`hermes backup` vs `hermes profile export`](../reference/faq.md#hermes-backup-vs-hermes-profile-export) explains which to use.
:::

---

## Prerequisites

For the POSIX source script, provide Git, curl, tar, and SHA-256 utilities.
Windows can bootstrap its pinned Git for Windows archive when Git is absent.
The script always downloads its verified uv pin; a uv already on your PATH is never used.

Current first-party installations run on **Python 3.14**. The broader
`>=3.11,<3.15` range in `pyproject.toml` lets older Python installations
run the updater before PM switches them to 3.14; it does not promise current
runtime support on 3.11–3.13. PM selects the managed tool versions from
`pm/lock.json`; it does not adopt arbitrary system Node versions as the
installed runtime.

Source builds can require a native compiler and platform development libraries.
Building Electron from source adds Node native-module requirements. These
build prerequisites do not apply to installing a complete desktop package.
Linux Chromium also requires system libraries supplied by the distribution.

:::tip Nix users
Nix is **no longer an explicitly supported install path** (best-effort only). If you already use Nix (on NixOS, macOS, or Linux), there's a dedicated setup path with a Nix flake, declarative NixOS module, and optional container mode. See the **[Nix & NixOS Setup](./nix-setup.md)** guide.
:::

---

## Manual / Developer Installation

For a source checkout, start with the
[PM developer workflow](../reference/package-management.md#developer-workflow).
It covers activation, daily commands, dependency refresh, and current bootstrap limits.
[Development Setup](../developer-guide/contributing.md#development-setup) covers the separate test environment and checks.

---

## Non-Sudo / System Service User Installs

Run the source installer as the intended service user. Its home, tool store,
configuration, and launcher must belong to that user.

1. As an administrator, install the source-build prerequisites and any Linux
   libraries needed by the selected browser backend.
2. As the service user, run the regular installer:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/majorissuerep/hermes-agent/main/scripts/install.sh | bash
   ```

3. Add the actual launcher directory to the service user's shell environment:

   ```bash
   export PATH="$HOME/.local/bin:$PATH"
   ```

4. Run `hermes doctor` from that account. Use the installed wrapper, not a
   hardcoded `venv/bin/hermes` path.
5. For a Linux user service that must survive logout, enable lingering as an administrator:

   ```bash
   sudo loginctl enable-linger SERVICE_USER
   ```

The current source installer does not run Playwright's `--with-deps` step or
provide a package-manager-specific sudo fallback. PM manages tool binaries;
the administrator supplies system libraries. See
[Browser automation](../user-guide/features/browser.md) and
[Messaging Gateway](../user-guide/messaging/index.md).

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `hermes: command not found` | Reload your shell (`source ~/.bashrc`) or check PATH |
| `API key not set` | Run `hermes model` to configure your provider, or `hermes config set OPENROUTER_API_KEY your_key` |
| Missing config after update | Run `hermes config check` then `hermes config migrate` |

For more diagnostics, run `hermes doctor` — it will tell you exactly what's missing and how to fix it.

### Symlinked home directories and external storage

Hermes supports a symlinked `HERMES_HOME` and symlinked home subdirectories,
including `hooks`, `skills`, `sessions`, and `logs`. During home initialization,
existing directory links are preserved, and permissions on linked directories
(and descendants such as `logs/curator`) are left to their owner.

If a link target is missing, inaccessible, or not a directory, initialization
stops with a storage error naming the path and link target. Hermes does **not**
replace the link or create its missing target: doing so could write data onto
the local disk while an external or NAS volume is unmounted. Check the reported
link, restore the mount or correct its target, and verify access permissions
before retrying. For a deliberately new dotfiles target, create it yourself only
after confirming the intended storage is available.

`hermes doctor` reports these failures as storage problems, not invalid YAML.
Keep your existing `config.yaml`; running `hermes setup` is not the repair for an
unavailable directory. This is a directory-availability check, not a mount monitor:
an existing directory cannot establish that the intended volume is mounted.

## Install method auto-detection

The update owner depends on the running installation, not only its data home.
Source checkouts use the managed Git update path. Desktop bundles, Docker,
Nix, and Termux packages retain their package owner's update mechanism.
`hermes doctor` reports installation provenance. See
[Updating & Uninstalling](./updating.md) before changing package-owned files.
