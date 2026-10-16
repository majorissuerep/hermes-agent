"""The connection/protocol qualification contract, shared by CI and HTTP E2E."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Connection:
    identity: str
    provider: str
    modes: tuple[str, ...]


CHAT = "chat_completions"
RESPONSES = "codex_responses"
ANTHROPIC = "anthropic_messages"

CONNECTIONS = {
    "litellm": Connection("litellm", "litellm", (CHAT, RESPONSES, ANTHROPIC)),
    "custom_openai": Connection("custom", "custom", (CHAT, RESPONSES)),
    "custom_anthropic": Connection("custom", "custom", (ANTHROPIC,)),
    "anthropic": Connection("anthropic", "anthropic", (ANTHROPIC,)),
    "openai": Connection("openai-api", "openai", (CHAT, RESPONSES)),
    "chatgpt": Connection("openai-codex", "openai-codex", (RESPONSES,)),
    "zai": Connection("zai", "zai", (CHAT,)),
    "grok": Connection("xai", "xai", (CHAT, RESPONSES)),
    "grok_subscription": Connection("xai-oauth", "xai-oauth", (RESPONSES,)),
    "vercel": Connection("ai-gateway", "ai-gateway", (CHAT,)),
}
