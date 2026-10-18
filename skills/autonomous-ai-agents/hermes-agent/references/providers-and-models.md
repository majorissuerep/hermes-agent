# Providers & Model Aliases

Set via `hermes model` or `hermes setup`. This fork intentionally ships a small
provider set. Provider plugins installed explicitly by the user remain an extension
point; vendor models can still be served through LiteLLM, Vercel or a custom endpoint.

| Provider | Authentication |
|---|---|
| `openai-codex` (ChatGPT/Codex) | Subscription OAuth, `hermes auth add openai-codex` |
| `openai-api` | `OPENAI_API_KEY`, or use a custom OpenAI endpoint |
| `anthropic` | `ANTHROPIC_API_KEY` or existing Claude Code authentication |
| `litellm` | Configured gateway + `LITELLM_API_KEY` / `ML_GATEWAY_API_KEY` |
| `custom` / `custom:<name>` | OpenAI- or Anthropic-compatible endpoint + `key_env` |
| `zai` | `GLM_API_KEY` / `ZAI_API_KEY` / `Z_AI_API_KEY` |
| `xai` / `xai-oauth` (Grok) | `XAI_API_KEY` or subscription OAuth |
| `ai-gateway` (Vercel) | `AI_GATEWAY_API_KEY` |

Fireworks and other direct providers are not bundled. Existing credentials are
preserved during updates but do not silently select a removed provider. Select a
supported route or configure an explicit custom endpoint; do not erase auth state.

Gateway configuration (save with `hermes config set`; do not edit ciphertext):

```yaml
model:
  provider: litellm
  default: your-gateway-model
providers:
  litellm:
    base_url: https://gateway.example.com/v1
    key_env: LITELLM_API_KEY
  private-claude:
    base_url: https://claude.example.com
    api_mode: anthropic_messages
    key_env: PRIVATE_CLAUDE_API_KEY
```

Keep API keys in the encrypted credential store, not the YAML. The base URL is a
non-secret setting. For no-prompt changes inside Hermes, see `encrypted-self-management.md`.

Multiple credentials per provider pool and rotate automatically (`hermes auth`).
Fallback chain when the primary fails: `hermes fallback add|remove|list`.

### User-defined model aliases

Work with `/model <name>` in the local TUI. Resolved by
`hermes_cli/model_switch.py::resolve_alias()`; user aliases are checked BEFORE
the built-in table, so a user `sonnet`/`grok` shadows the built-in.

```yaml
# Full form
model_aliases:
  fav:
    model: claude-sonnet-4.6
    provider: anthropic
  local-qwen:
    model: qwen3.5:397b
    provider: custom
    base_url: "https://ollama.com/v1"
  theta:
    model: theta-1
    provider: custom
    base_url: "https://theta.example.com/v1"
    key_env: THETA_API_KEY        # or: api_key: "${THETA_API_KEY}"

# Short form ("provider/model"), also via CLI:
#   hermes config set model.aliases.fav anthropic/claude-sonnet-4.6
model:
  aliases:
    fav: anthropic/claude-sonnet-4.6
```

`/model fav` — session-scoped; add `--global` to persist as default.

An alias with its own `base_url` authenticates with its own credential
(`api_key`, which also accepts a `"${VAR}"` reference, or `key_env`). With
neither set the key is resolved from the alias HOST, never carried over from
the provider that was active before the switch.

Built-in aliases (catalog-resolved against the active provider): `sonnet`,
`opus`, `haiku`, `claude`, `gpt5`, `gpt`, `codex`, `o3`, `o4`, `gemini`,
`deepseek`, `grok`, `llama`, `qwen`, `minimax`, `nemotron`, `kimi`, `glm`,
`step`, `mimo`, `trinity`.
