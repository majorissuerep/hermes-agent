---
title: "Integrations"
sidebar_label: "Overview"
sidebar_position: 0
---

# Integrations

Hermes Agent connects to external systems for AI inference, tool servers, IDE workflows, programmatic access, and more. These integrations extend what Hermes can do and where it can run.

:::tip Start here
If you only have time to set up one integration, set up [Nous Portal](./nous-portal.md) — a single OAuth login covers 300+ models plus the four Tool Gateway tools (web search, image generation, TTS, and browser automation).
:::

## AI Providers & Routing

Hermes supports multiple AI inference providers out of the box. Use `hermes model` to configure interactively, or set them in `config.yaml`.

- **[AI Providers](./providers.md)** — OpenRouter, Anthropic, OpenAI, Google, and any OpenAI-compatible endpoint. Hermes auto-detects capabilities like vision, streaming, and tool use per provider.
- **[Provider Routing](../user-guide/features/provider-routing.md)** — Fine-grained control over which underlying providers handle your OpenRouter requests. Optimize for cost, speed, or quality with sorting, whitelists, blacklists, and explicit priority ordering.
- **[Fallback Providers](../user-guide/features/fallback-providers.md)** — Automatic failover to backup LLM providers when your primary model encounters errors. Includes primary model fallback and independent auxiliary task fallback for vision, compression, and web extraction.

## Tool Servers (MCP)

- **[MCP Servers](../user-guide/features/mcp.md)** — Connect Hermes to external tool servers via Model Context Protocol. Access tools from GitHub, databases, file systems, browser stacks, internal APIs, and more without writing native Hermes tools. Supports both stdio and SSE transports, per-server tool filtering, and capability-aware resource/prompt registration.

## Web Search Backends

The `web_search` and `web_extract` tools support eight backend providers, configured via `config.yaml` or `hermes tools`:

| Backend | Env Var | Search | Extract | Crawl |
|---------|---------|--------|---------|-------|
| **Firecrawl** (default) | `FIRECRAWL_API_KEY` | ✔ | ✔ | ✔ |
| **SearXNG** | `SEARXNG_URL` | ✔ | — | — |
| **Brave** (free tier) | `BRAVE_SEARCH_API_KEY` | ✔ | — | — |
| **DuckDuckGo** (ddgs) | _(none)_ | ✔ | — | — |
| **Exa** | `EXA_API_KEY` | ✔ | ✔ | — |
| **Parallel** | `PARALLEL_API_KEY` | ✔ | ✔ | — |
| **xAI** | `XAI_API_KEY` | ✔ | — | — |

Quick setup example:

```yaml
web:
  backend: firecrawl    # firecrawl | searxng | brave-free | ddgs | tavily | perplexity | keenable | exa | parallel | xai
```

If `web.backend` is not set, the backend is auto-detected from whichever API key is available. Self-hosted Firecrawl is also supported via `FIRECRAWL_API_URL`.

## Browser Automation

Hermes includes full browser automation with multiple backend options for navigating websites, filling forms, and extracting information:

- **Browser Use Cloud** — Managed Chromium with stealth, residential proxies, CAPTCHA solving, and reusable browser profiles
- **Browserbase** — Alternative cloud browser provider with managed browsers, anti-bot tooling, CAPTCHA solving, and residential proxies
- **Local Chromium-family CDP** — Connect to your running Chrome, Brave, Chromium, or Edge browser using `/browser connect`
- **Local Chromium** — Headless local browser via the `agent-browser` CLI

See [Browser Automation](../user-guide/features/browser.md) for setup and usage.

## Voice & TTS Providers

Text-to-speech and speech-to-text for local conversations:

| Provider | Quality | Cost | API Key |
|----------|---------|------|---------|
| **Edge TTS** (default) | Good | Free | None needed |
| **ElevenLabs** | Excellent | Paid | `ELEVENLABS_API_KEY` |
| **OpenAI TTS** | Good | Paid | `VOICE_TOOLS_OPENAI_KEY` |
| **MiniMax** | Good | Paid | `MINIMAX_API_KEY` |
| **xAI TTS** | Good | Paid | `XAI_API_KEY` |
| **NeuTTS** | Good | Free | None needed |

Speech-to-text supports eight providers: local faster-whisper (free, runs on-device), a local command wrapper, Groq, OpenAI Whisper API, Mistral, xAI, ElevenLabs Scribe, and DeepInfra. See [Voice & TTS](../user-guide/features/tts.md) and [Voice Mode](../user-guide/features/voice-mode.md) for details.

## Memory & Personalization

- **[Built-in Memory](../user-guide/features/memory.md)** — Persistent, curated memory via `MEMORY.md` and `USER.md` files. The agent maintains bounded stores of personal notes and user profile data that survive across sessions.
- **[Memory Providers](../user-guide/features/memory-providers.md)** — Plug in external memory backends for deeper personalization. Two providers ship with Hermes: RetainDB (hybrid search) and ByteRover (CLI-based); Honcho (dialectic reasoning), Hindsight (knowledge graphs), Supermemory (semantic recall), Mem0 (cloud extraction), OpenViking (tiered retrieval) and Holographic (local SQLite) are available from the plugin catalog via `hermes plugins install <name>`.

## Plugins

- **[Plugin System](../user-guide/features/plugins.md)** — Extend Hermes with custom tools, lifecycle hooks, and CLI commands without modifying core code. Plugins are discovered from `~/.hermes/plugins/`, project-local `.hermes/plugins/`, and pip-installed entry points.
- **[Build a Plugin](../developer-guide/plugins/index.md)** — Step-by-step guide for creating Hermes plugins with tools, hooks, and CLI commands.

## Training & Evaluation

- **[Batch Processing](../user-guide/features/batch-processing.md)** — Run the agent across hundreds of prompts in parallel, generating structured ShareGPT-format trajectory data for training data generation or evaluation.
