---
title: Sandbox
description: Kernel-enforced, default-deny isolation for everything the model runs, with per-session grants for files, tools and network.
---

# Sandbox

With the sandbox on, the model starts with **no tools and no access to your files**.
You grant exactly what a session needs: a project folder, your `.zshrc`, network
access, the terminal tool. The operating system kernel enforces it, not a pattern
list. Every command the model runs is confined, including file-tool operations,
background processes, `execute_code` and subagents. This holds even if the model is
prompt-injected.

| | Linux | macOS |
|---|---|---|
| Mechanism | Landlock (filesystem) + seccomp (sockets, keyring, io_uring) | Seatbelt profile (`sandbox-exec`) |
| Needs | kernel ≥ 5.13 with Landlock (most distros since 2022) | any supported macOS |
| Root / new users | no | no |

Hermes itself (your encrypted vault, sessions, provider keys, and the sandbox's own
configuration) is never confined and never reachable from inside:

- **No grant can reach the Hermes home, even a grant covering it.** Granting `~:rw` grants
  everything in `~` *except* `~/.hermes`.
- **The vault's unlock credentials are removed** from every sandboxed environment
  (`HERMES_MASTER_PASSWORD`, `HERMES_VAULT_PRIVATE_KEY`).
- **If config.yaml disappears or becomes unreadable**, a session that had the sandbox on
  keeps it on. Only an explicit `enabled: false` turns it off.

## Set up

```bash
hermes sandbox setup      # pick default presets, enable, run the live self-check
# or, non-interactively:
hermes sandbox enable --preset coding
hermes sandbox check      # proves enforcement on THIS machine (8 probes)
```

`hermes sandbox check` launches real confined processes and reports PASS or FAIL for each
probe:

- reading and writing inside a grant are allowed;
- reading and writing outside it are denied;
- `~` and the Hermes home cannot be read;
- no UNIX socket and no network socket can be opened.

The setting applies to **new** sessions.

## Per-session grants: `/sandbox`

Inside the CLI (or a messaging chat, where changing anything requires a gateway admin):

```
/sandbox                         show backend, tools, grants, network for this session
/sandbox grant ~/code/app:rw     read-write a project (scanned for secrets first)
/sandbox grant ~/.zshrc          read-only; a single file works too
/sandbox grant ~/.env --force    grant despite the scan finding secrets
/sandbox revoke ~/code/app       take it back (also works for config/preset grants)
/sandbox preset coding           apply a preset
/sandbox net on                  let sandboxed processes use the network
/sandbox tools add web --now     allow a tool (a new session starts, see below)
/sandbox scan ~/Downloads        what would a grant expose?
/sandbox reset                   drop this session's changes
```

**File, network and socket changes apply to the next command**, with no restart. They
never touch the prompt, so they cost nothing in prompt caching.

**Tool changes apply to the next session**, because they change the tool list the model
sees. `--now` starts one immediately, the same way `/tools enable` does. Hermes never
changes the tool list in the middle of a conversation.

Grants are ordinary paths:

- `PATH` is read-only (read and execute) and covers everything beneath it.
- `PATH:rw` also allows writing.
- `@cwd` means the session's working directory. It grants nothing when that directory is
  your home or `/`: "workspace" means a project, not everything you own. Grant `~` explicitly
  if you really mean it.
- `@path` means the toolchain directories on your `PATH`.

A grant for a file that does not exist yet confers nothing.

## Scanning

Before a grant takes effect, Hermes scans the target. The scan is bounded by file count,
bytes and time. It reports:

- **sensitive files by name**: SSH/GPG keys, cloud and registry credentials, `.env` files,
  password databases, browser credential stores;
- **secrets in file contents**, found by the same detector Hermes uses to redact tool
  output;
- **Hermes' own home or code**: write access to Hermes' code would let the model change
  what Hermes runs after a restart.

If the scan finds anything, the grant is refused until you repeat it with `--force`.
`hermes sandbox scan PATH` runs the scan alone. Set `sandbox.scan_on_grant: false` to
skip it.

## Presets

| Preset | Gives |
|---|---|
| `workspace` / `workspace-ro` | the session's working directory, rw / ro |
| `toolchains` | the directories on `PATH` (read + execute) and their install roots |
| `shell-rc` | `~/.profile`, `~/.bashrc`, `~/.zshrc`, … read-only |
| `git` | `~/.gitconfig`, `~/.config/git` read-only |
| `network` | network on |
| `files` | the file tools |
| `coding` | workspace + toolchains + shell-rc + git; terminal, file, code, todo, clarify tools |
| `research` | web, todo, clarify tools; no files |

Custom presets go in config.yaml:

```yaml
sandbox:
  enabled: true
  default_presets: [coding]
  tools: []                # extra allowed tools/toolsets ("*" = all)
  grants: ["~/notes", "~/code/shared:rw"]
  network: false
  unix_sockets: false
  subagents: inherit       # or readonly: subagents lose every write grant
  scan_on_grant: true
  presets:
    webdev:
      description: node project with network
      include: [coding]
      grants: ["~/.npmrc"]
      network: true
```

## What a sandboxed process can reach

A sandboxed process can reach only three things:

- **The read-only OS baseline** every program needs to start: `/usr`, `/bin`, `/lib*`,
  `/etc`, `/opt`, `/nix`, `/proc`, `/sys`, and `/dev/{null,zero,full,random,urandom}`.
  On macOS the equivalents are `/System`, `/Library`, `/private/etc` and Homebrew.
- **A private temp dir** (`TMPDIR`), mode 0700, one per session.
- **Your grants.** Nothing else.

Hermes also blocks:

- **UNIX sockets.** `/var/run/docker.sock` or the systemd/D-Bus session bus would let a
  confined process start unconfined ones. Turn them on with `/sandbox unix on` only when
  you mean it.
- **Kernel keyring and io_uring**, via seccomp.
- **`sudo` and other setuid programs**, which cannot gain privileges (`no_new_privs`).

Model tools that read files inside Hermes itself (vision, transcription, image inputs) obey
the same grants. What *you* attach or `@reference` is your choice and is not restricted.

## Limits

- **A grant that covers a protected path is split around it.** A grant on an ancestor of
  the Hermes home (e.g. `~:rw`) becomes grants on that directory's other entries. So the
  sandbox can create files *inside* those entries, but not new files directly in `~`.

- **File metadata:** `stat` still reveals that an ungranted path exists (Landlock does not
  mediate metadata). Contents and listings are denied.
- **Network is on or off, not per host.** A per-host allowlist needs a proxy (see `hermes
  egress` for the Docker backend).
- **Signals on Linux < 6.12:** a sandboxed process can still send signals to your other
  processes. That is a denial-of-service risk, not a data leak.
- **Other terminal backends:** the sandbox confines the local backend. Docker, Modal and
  similar backends are isolated by their own containers. SSH runs on the remote host, which
  this sandbox does not cover.
- **Project plugins** (`HERMES_ENABLE_PROJECT_PLUGINS`) are ignored while the sandbox is
  on. A sandboxed model can write the workspace, and code planted there must never load into
  the unconfined Hermes process. Hermes' automatic git probes already disable repository
  hooks, fsmonitor and attribute drivers.
- **Processes Hermes starts for itself** run unconfined: MCP servers you configure and the
  browser engine. The model's *tool access* to them still follows the tool allowlist.
- **macOS:** `sandbox-exec` is deprecated as a public API but still ships and is used by
  Chromium, Codex CLI and Claude Code.
- **Windows:** no backend. When the sandbox is enabled, commands are refused rather than run
  unconfined.
