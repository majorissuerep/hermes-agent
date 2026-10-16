---
sidebar_position: 1
title: "Hermes Agent Quickstart"
description: "Your first conversation with Hermes Agent — from install to chatting in under 5 minutes"
---

# Hermes Agent Quickstart

:::warning Unofficial fork
This repository is an **unofficial fork** of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent), maintained by [@majorissuerep](https://github.com/majorissuerep) — not built, supported, or endorsed by Nous Research. The install command on this page installs **this fork** from the fork's own git history; inspect it before running. Fork problems go to the [fork's issue tracker](https://github.com/majorissuerep/hermes-agent/issues), never upstream.
:::

Python dependency commands on this page use a
[PM-prepared source checkout](../reference/package-management.md#developer-workflow).
After a dependency change, reactivate the checkout and restart Hermes.

This guide gets you from zero to a working Hermes setup that survives real use. Install, choose a provider, verify a working chat, and know exactly what to do when something breaks.

## Prefer to watch?

**Onchain AI Garage** put together a Masterclass walkthrough of installation, setup, and basic commands — a good companion to this page if you'd rather follow along on video. For more, see the full [Hermes Agent Tutorials & Use Cases](https://www.youtube.com/playlist?list=PLmpUb_PWAkDxewld5ZYyKifuHxgIbiq2d) playlist.

<div style={{position: 'relative', paddingBottom: '56.25%', height: 0, overflow: 'hidden', maxWidth: '100%', marginBottom: '1.5rem'}}>
  <iframe
    style={{position: 'absolute', top: 0, left: 0, width: '100%', height: '100%'}}
    src="https://www.youtube-nocookie.com/embed/R3YOGfTBcQg"
    title="Hermes Agent Masterclass: Installation, Setup, Basic Commands"
    frameBorder="0"
    allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
    allowFullScreen
  ></iframe>
</div>

## Who this is for

- Brand new and want the shortest path to a working setup
- Switching providers and don't want to lose time to config mistakes
- Setting up Hermes for a team, bot, or always-on workflow
- Tired of "it installed, but it still does nothing"

## The fastest path

Pick the row that matches your goal:

| Goal | Do this first | Then do this |
|---|---|---|
| I just want Hermes working on my machine | `hermes setup` | Run a real chat and verify it responds |
| I already know my provider | `hermes model` | Save the config, then start chatting |
| I want a bot or always-on setup | `hermes gateway setup` after CLI works | Connect Telegram, Discord, Slack, or another platform |
| I want a local or self-hosted model | `hermes model` → custom endpoint | Verify the endpoint, model name, and context length |
| I want multi-provider fallback | `hermes model` first | Add routing and fallback only after the base chat works |

**Rule of thumb:** if Hermes cannot complete a normal chat, do not add more features yet. Get one clean conversation working first, then layer on gateway, cron, skills, voice, or routing.

---

## 1. Install Hermes Agent

The commands below install this fork's `main` branch. For an existing upstream installation, follow [the migration instructions](./installation.md#switch-existing-installation). Desktop downloads from the upstream website install upstream Hermes; use the fork's CLI installer here first.

#### Linux / macOS / WSL2
```bash
curl -fsSL https://raw.githubusercontent.com/majorissuerep/hermes-agent/main/scripts/install.sh | bash
```

After it finishes, reload your shell:

```bash
source ~/.bashrc   # or source ~/.zshrc
```

Check `hermes secure-vault status`. If no vault exists, run `hermes secure-vault migrate` from a terminal and choose a master password before continuing. The fork requires an unlocked vault for commands that access state. See [encrypted-state setup](./installation.md#initialize-encrypted-state) for backups and unattended services.

Once the vault is ready, `hermes desktop` builds and launches Desktop from the fork's installed checkout.

For detailed installation options, prerequisites, and troubleshooting, see the [Installation guide](./installation.md).

## 2. Choose a Provider

The single most important setup step. Use `hermes model` to walk through the choice interactively:

```bash
hermes model
```

:::info Setup modes
On a fresh install, `hermes setup` offers two modes:

- **Setup** — choose an explicit provider and configure your tools.
- **Blank Slate** — start with only provider/model, file operations, terminal, vision,
  and the essential Hermes skill. Memory, delegation, cron, optional plugins, MCP,
  compression, checkpoints, and smart routing stay off until you enable them.

Use `hermes tools` to enable delegation or other toolsets later. The bundled
Hermes skill remains available to help configure the encrypted installation.
:::

This fork bundles these connections only:

| Connection | Setup |
|---|---|
| **ChatGPT / Codex** | Subscription sign-in through `hermes model` |
| **Anthropic / Claude** | Anthropic API key or supported Claude authentication |
| **OpenAI API** | OpenAI API key through `hermes model` |
| **LiteLLM** | Your gateway URL and key |
| **Custom OpenAI / Anthropic** | Explicit endpoint, wire protocol, and credential |
| **Z.AI / GLM** | Z.AI API key |
| **Grok** | xAI API key or subscription OAuth |
| **Vercel AI Gateway** | Vercel gateway key |

Fireworks and other direct providers are not bundled. Existing credentials survive
updates, but retired providers require an explicit supported route or provider plugin.
Models from other vendors can still run through your chosen gateway or custom endpoint.
See [AI Providers](../integrations/providers.md) for provider IDs and configuration.

:::caution Minimum context: 64K tokens
Hermes Agent requires a model with at least **64,000 tokens** of context. Models with smaller windows cannot maintain enough working memory for multi-step tool-calling workflows and will be rejected at startup. Most hosted models (Claude, GPT, Gemini, Qwen, DeepSeek) meet this easily. If you're running a local model, set its context size to at least 64K (e.g. `--ctx-size 65536` for llama.cpp or `-c 65536` for Ollama).
:::

:::tip
You can switch providers at any time with `hermes model` — no lock-in. For a full list of all supported providers and setup details, see [AI Providers](../integrations/providers.md).
:::

### How settings are stored

Hermes separates secrets from settings; both files are encrypted at rest in this fork:

- **Secrets and tokens** → `~/.hermes/.env`
- **Non-secret settings** → `~/.hermes/config.yaml`

The easiest way to set values correctly is through the CLI:

```bash
hermes config set model.default claude-opus-4.6
hermes config set terminal.backend docker
hermes model  # Enter credentials through the interactive secret prompt
```

The right value goes through its encrypted writer automatically. Never overwrite these
files as plaintext. Unlock once at startup; local commands and delegated agents reuse
that session authority without another passphrase. A standalone update asks once.

## 3. Run Your First Chat

```bash
hermes            # classic CLI
hermes --tui      # modern TUI (recommended)
```

You'll see a welcome banner with your model, available tools, and skills. Use a prompt that's specific and easy to verify:

:::tip Pick your interface
Hermes ships with two terminal interfaces: the classic `prompt_toolkit` CLI and a newer [TUI](../user-guide/tui.md) with modal overlays, mouse selection, and non-blocking input. Both share the same sessions, slash commands, and config — try each with `hermes` vs `hermes --tui`.
:::

```
Summarize this repo in 5 bullets and tell me what the main entrypoint is.
```

```
Check my current directory and tell me what looks like the main project file.
```

```
Help me set up a clean GitHub PR workflow for this codebase.
```

**What success looks like:**

- The banner shows your chosen model/provider
- Hermes replies without error
- It can use a tool if needed (terminal, file read, web search)
- The conversation continues normally for more than one turn

If that works, you're past the hardest part.

## 4. Verify Sessions Work

Before moving on, make sure resume works:

```bash
hermes --continue    # Resume the most recent session
hermes -c            # Short form
```

That should bring you back to the session you just had. If it doesn't, check whether you're in the same profile and whether the session actually saved. This matters later when you're juggling multiple setups or machines.

## 5. Try Key Features

### Use the terminal

```
❯ What's my disk usage? Show the top 5 largest directories.
```

The agent runs terminal commands on your behalf and shows results.

### Slash commands

Type `/` to see an autocomplete dropdown of all commands:

| Command | What it does |
|---------|-------------|
| `/help` | Show all available commands |
| `/tools` | List available tools |
| `/model` | Switch models interactively |
| `/personality pirate` | Try a fun personality |
| `/save` | Save the conversation |

### Multi-line input

Press `Alt+Enter`, `Ctrl+J`, or `Shift+Enter` to add a new line. `Shift+Enter` requires a terminal that sends it as a distinct sequence (Kitty / foot / WezTerm / Ghostty by default; iTerm2 / Alacritty / VS Code terminal once the Kitty keyboard protocol is enabled). `Alt+Enter` and `Ctrl+J` work in every terminal.

### Interrupt the agent

If the agent is taking too long, type a new message and press Enter — it interrupts the current task and switches to your new instructions. `Ctrl+C` also works.

## 6. Add the Next Layer

Only after the base chat works. Pick what you need:

### Bot or shared assistant

```bash
hermes gateway setup    # Interactive platform configuration
```

Connect [Telegram](../user-guide/messaging/telegram.md), [Discord](../user-guide/messaging/discord.md), [Slack](../user-guide/messaging/slack.md), [WhatsApp](../user-guide/messaging/whatsapp.md), [Signal](../user-guide/messaging/signal.md), [Email](../user-guide/messaging/email.md), [Home Assistant](../user-guide/messaging/homeassistant.md) (plugin), or [Microsoft Teams](../user-guide/messaging/teams.md).

### Automation and tools

- `hermes tools` — tune tool access per platform
- `hermes skills` — browse and install reusable workflows
- Cron — only after your bot or CLI setup is stable

### Sandboxed terminal

For safety, run the agent in a Docker container or on a remote server:

```bash
hermes config set terminal.backend docker    # Docker isolation
hermes config set terminal.backend ssh       # Remote server
```

For Docker sandboxes, you can also enable the **egress credential-injection proxy** so the sandbox never sees your real API keys — only opaque proxy tokens that work exclusively from behind a local TLS-intercepting daemon. See [Egress proxy](../user-guide/egress/iron-proxy.md). Setup is `hermes egress setup && hermes egress start`; `hermes setup terminal` also points Docker users at it. Modal, SSH, Daytona, and Singularity are not wired yet.

### Voice mode

Run `hermes tools` and configure the Voice providers. Then enable `/voice on`
in the CLI and press `Ctrl+B` to record. PM handles missing supported
requirements; a dependency change can require a restart. Local Faster-Whisper
is not available on every architecture. See [Voice Mode](../user-guide/features/voice-mode.md).

### Skills

Skills are on-demand instruction documents that teach Hermes how to do a specific task — deploy to Kubernetes, open a GitHub PR, fine-tune a model, search for GIFs. Each is a `SKILL.md` file with a name, a description, and a step-by-step procedure. The agent reads the short descriptions for free and only loads a skill's full content when a task actually calls for it, so adding skills doesn't bloat every request.

Hermes ships with a catalog of bundled skills already installed in `~/.hermes/skills/`. You can add more from the Skills Hub, or write your own.

**Browse and install from the hub:**

```bash
hermes skills browse                      # list everything available
hermes skills search kubernetes           # find skills by keyword
hermes skills install openai/skills/k8s   # install one (runs a security scan first)
```

The install argument is a `source/path` slug from the hub — `openai/skills/k8s` means the `k8s` skill from OpenAI's catalog. `hermes skills browse` shows the exact slugs to use.

**Use a skill** — every installed skill becomes a slash command automatically:

```bash
/k8s deploy the staging manifest          # run the skill with a request
/k8s                                       # load it and let Hermes ask what you need
```

This works in the CLI and in any connected messaging platform. You don't have to install everything up front — the agent picks the right bundled skill on its own during normal conversation when a task matches one.

See [Skills System](../user-guide/features/skills.md) for writing your own, external skill directories, and the full hub source list.

### MCP servers

```yaml
# Add to ~/.hermes/config.yaml
mcp_servers:
  github:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "ghp_xxx"
```

### Editor integration (ACP)

ACP support ships with the standard `[all]` extras, so the curl installer already includes it. Just run:

```bash
hermes acp
```

(If you installed without `[all]`, run `cd ~/.hermes/hermes-agent && python -c "import pm; pm.sync_venv(['acp'], explicit=True)"` first.)

See [ACP Editor Integration](../user-guide/features/acp.md).

---

## Common Failure Modes

These are the problems that waste the most time:

| Symptom | Likely cause | Fix |
|---|---|---|
| Hermes opens but gives empty or broken replies | Provider auth or model selection is wrong | Run `hermes model` again and confirm provider, model, and auth |
| Custom endpoint "works" but returns garbage | Wrong base URL, model name, or not actually OpenAI-compatible | Verify the endpoint in a separate client first |
| Gateway starts but nobody can message it | Bot token, allowlist, or platform setup is incomplete | Re-run `hermes gateway setup` and check `hermes gateway status` |
| `hermes --continue` can't find old session | Switched profiles or session never saved | Check `hermes sessions list` and confirm you're in the right profile |
| Model unavailable or odd fallback behavior | Provider routing or fallback settings are too aggressive | Keep routing off until the base provider is stable |
| `hermes doctor` flags config problems | Config values are missing or stale | Fix the config, retest a plain chat before adding features |

## Recovery Toolkit

When something feels off, use this order:

1. `hermes doctor`
2. `hermes model`
3. `hermes setup`
4. `hermes sessions list`
5. `hermes --continue`
6. `hermes gateway status`

That sequence gets you from "broken vibes" back to a known state fast.

---

## Quick Reference

| Command | Description |
|---------|-------------|
| `hermes` | Start chatting |
| `hermes model` | Choose your LLM provider and model |
| `hermes tools` | Configure which tools are enabled per platform |
| `hermes setup` | Full setup wizard (configures everything at once) |
| `hermes doctor` | Diagnose issues |
| `hermes update` | Update to latest version |
| `hermes gateway` | Start the messaging gateway |
| `hermes --continue` | Resume last session |

## Next Steps

- **[CLI Guide](../user-guide/cli.md)** — Master the terminal interface
- **[Configuration](../user-guide/configuration.md)** — Customize your setup
- **[Messaging Gateway](../user-guide/messaging/index.md)** — Connect Telegram, Discord, Slack, WhatsApp, Signal, Email, Home Assistant, Teams, and more
- **[Tools & Toolsets](../user-guide/features/tools.md)** — Explore available capabilities
- **[AI Providers](../integrations/providers.md)** — Full provider list and setup details
- **[Skills System](../user-guide/features/skills.md)** — Reusable workflows and knowledge
- **[Tips & Best Practices](../guides/tips.md)** — Power user tips
- **[Moving to another machine](../reference/faq.md#exporting-hermes-to-another-machine)** — `hermes backup` migrates your whole setup (or [a single profile](../reference/faq.md#moving-a-single-profile-to-another-machine)); no need to rebuild from scratch
