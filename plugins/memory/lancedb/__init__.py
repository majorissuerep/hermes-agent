"""LanceDB memory — encrypted, local, cross-session memory with hybrid search and navigation.

Everything stays on the machine and inside the vault: records (facts, past turns, delegations)
are sealed into a ``hermes_security.frames`` log, embeddings come from a local ONNX model, and the
LanceDB index lives only in RAM (see ``store.py`` for why). Each turn recalls relevant memories
from OTHER sessions (hybrid vector + BM25 with a similarity floor); the model digs deeper and walks
sessions/timelines with ``lance_recall`` / ``lance_memory``.

Config (config.yaml, ``plugins.lancedb-memory``): embedding_model, capture_turns (true),
mirror_builtin (true), recall_limit (4), recall_min_similarity (0.25).
"""

from __future__ import annotations

import logging
from contextlib import suppress
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider, RecallStatus, is_trivial_prompt, spawn_context_thread
from tools.registry import tool_error

logger = logging.getLogger(__name__)

CONFIG_KEY = "lancedb-memory"
_DEFAULTS = {
    "embedding_model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "capture_turns": True,
    "mirror_builtin": True,
    "recall_limit": 4,
    # Cosine floor for auto-recall, calibrated on the default model: unrelated prompts peaked at
    # 0.23, genuine matches sat at 0.3–0.6. Tools apply no floor.
    "recall_min_similarity": 0.25,
}
_USER_CHARS, _ASSISTANT_CHARS, _DELEGATION_CHARS = 1200, 1800, 1500

_PROMPT_BLOCK = (
    "# Long-term Memory (LanceDB, encrypted)\n"
    "You remember past sessions. Relevant memories from earlier conversations may be recalled into a turn "
    "automatically, but that is only a sample: call lance_recall before answering anything that could depend on "
    "prior work, the user's preferences, people, projects or decisions, and search again with different wording "
    "for multi-part questions. Open a hit with lance_memory(action='get'), follow lance_memory(action='related') "
    "or replay its session with action='session'; browse with 'timeline' and 'sessions'. Store durable facts "
    "with lance_memory(action='remember') as soon as the user states them; correct with 'update' rather than "
    "duplicating; 'forget' erases permanently."
)


def _truthy(value: Any) -> bool:
    return value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on")


def _load_plugin_config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import cfg_get, load_config_readonly

        raw = cfg_get(load_config_readonly(), "plugins", CONFIG_KEY, default={}) or {}
    except Exception:
        raw = {}
    return {**_DEFAULTS, **{k: v for k, v in raw.items() if v not in (None, "")}}


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


class LanceMemoryProvider(MemoryProvider):
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self._config = dict(config or _DEFAULTS)
        self._store = None
        self._error = ""
        self._session_id = ""
        self._platform = ""
        self._writes_enabled = True
        self._last_recall = 0

    @property
    def name(self) -> str:
        return "lancedb"

    # -- availability / setup ----------------------------------------------------------

    def is_available(self) -> bool:
        from hermes_constants import get_hermes_home
        from hermes_security.io import _home_for  # crypto-free metadata probe

        return _home_for(get_hermes_home()) is not None

    def unavailable_reason(self) -> str:
        return ("LanceDB memory keeps everything encrypted with the Hermes vault, and this home has none: "
                "run `hermes secure-vault migrate` first.")

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {"key": "embedding_model", "description": "Local fastembed model (multilingual default; changing it re-embeds in the background)",
             "default": _DEFAULTS["embedding_model"]},
            {"key": "capture_turns", "description": "Remember every conversation turn for cross-session recall",
             "default": "true", "choices": ["true", "false"]},
            {"key": "mirror_builtin", "description": "Mirror built-in memory tool writes as facts",
             "default": "true", "choices": ["true", "false"]},
            {"key": "recall_limit", "description": "Memories auto-recalled per turn (0 disables)", "default": "4", "type": "integer",
             "minimum": 0, "maximum": 20},
            {"key": "recall_min_similarity", "description": "Cosine floor for auto-recall", "default": "0.25", "type": "number",
             "minimum": 0, "maximum": 1, "step": 0.05},
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        from hermes_cli.config import save_config

        save_config({"plugins": {CONFIG_KEY: dict(values)}}, merge_existing=True)

    # -- lifecycle ------------------------------------------------------------------------

    def initialize(self, session_id: str, **kwargs) -> None:
        self._config = _load_plugin_config()
        self._session_id = session_id or ""
        self._platform = str(kwargs.get("platform") or "")
        # Subagents, cron and flush agents read memory but never write turns into it.
        self._writes_enabled = str(kwargs.get("agent_context") or "primary") == "primary"
        home = Path(kwargs.get("hermes_home") or self._home())
        with suppress(Exception):
            from tools.lazy_deps import ensure

            ensure("memory.lancedb", prompt=False)
        try:
            from .embedder import get_embedder, model_dim
            from .store import get_store

            model = str(self._config["embedding_model"])
            embedder = get_embedder(model, home / "cache" / "fastembed")
            self._store = get_store(home, embedder, model_dim(model))
        except Exception as exc:
            self._store, self._error = None, f"{type(exc).__name__}: {exc}"
            logger.warning("LanceDB memory unavailable: %s", self._error)
            return
        spawn_context_thread(self._backfill_when_ready, name="lancedb-memory-backfill").start()

    @staticmethod
    def _home() -> str:
        from hermes_constants import get_hermes_home

        return str(get_hermes_home())

    def _backfill_when_ready(self) -> None:
        store = self._store
        if store is not None and store.embedder.wait():
            with suppress(Exception):
                if n := store.backfill_vectors():
                    logger.info("LanceDB memory: embedded %d stored memories", n)

    def system_prompt_block(self) -> str:
        return _PROMPT_BLOCK if self._store is not None else ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        self._last_recall = 0
        limit = int(self._config.get("recall_limit") or 0)
        if self._store is None or limit <= 0 or is_trivial_prompt(query) or not self._store.embedder.ready:
            return ""
        where = self._store.build_where(exclude_session_id=session_id or self._session_id)
        hits = self._store.search(query, limit=limit, where=where,
                                  min_similarity=float(self._config.get("recall_min_similarity", 0.25)))
        if not hits:
            return ""
        from .tools import present

        lines = []
        for hit in hits:
            item = present(hit)
            text = item["text"].replace("\n", " ⏎ ")
            lines.append(f"- [{item['kind']} · {item['when']} · id {item['id']}] {text}")
        self._last_recall = len(lines)
        return "## Recalled from past sessions (LanceDB memory)\n" + "\n".join(lines)

    def recall_status(self) -> Optional[RecallStatus]:
        return RecallStatus("LanceDB", self._last_recall) if self._last_recall else None

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "", **kwargs) -> None:
        # Runs on the MemoryManager's serialized background worker: blocking here is fine.
        if (self._store is None or not self._writes_enabled or not _truthy(self._config.get("capture_turns", True))
                or is_trivial_prompt(user_content) or not (assistant_content or "").strip()):
            return
        text = f"User: {_clip(user_content, _USER_CHARS)}\nAssistant: {_clip(assistant_content, _ASSISTANT_CHARS)}"
        self._store.put(kind="turn", text=text, session_id=session_id or self._session_id, platform=self._platform,
                        source="conversation")

    def on_session_switch(self, new_session_id: str, *, parent_session_id: str = "", reset: bool = False,
                          rewound: bool = False, **kwargs) -> None:
        self._session_id = new_session_id or self._session_id

    def on_memory_write(self, action: str, target: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Mirror the built-in MEMORY.md / USER.md tool so those facts are searchable here too."""
        if self._store is None or not self._writes_enabled or not _truthy(self._config.get("mirror_builtin", True)):
            return
        source = f"builtin:{target}"
        old_text = str((metadata or {}).get("old_text") or "")
        match = None
        if old_text and action in ("replace", "remove"):
            match = next((r for r in self._store.records() if r.get("source") == source and old_text in r["text"]), None)
        if action == "remove":
            if match:
                self._store.erase([match["id"]])
        elif content.strip():
            self._store.put(kind="fact", text=content.strip(), tags=[target], source=source,
                            session_id=self._session_id, platform=self._platform,
                            record_id=match["id"] if match else "")

    def on_delegation(self, task: str, result: str, *, child_session_id: str = "", **kwargs) -> None:
        if self._store is None or not self._writes_enabled or not (task or "").strip():
            return
        text = f"Delegated task: {_clip(task, _DELEGATION_CHARS // 2)}\nResult: {_clip(result, _DELEGATION_CHARS)}"
        self._store.put(kind="delegation", text=text, session_id=self._session_id, platform=self._platform,
                        source=f"subagent:{child_session_id}" if child_session_id else "subagent")

    # -- tools ----------------------------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        from .tools import TOOL_SCHEMAS

        return list(TOOL_SCHEMAS)

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        from .tools import HANDLERS

        if tool_name not in HANDLERS:
            return tool_error(f"Unknown tool: {tool_name}")
        if self._store is None:
            return tool_error(f"LanceDB memory is not available: {self._error or 'not initialized'}")
        try:
            return HANDLERS[tool_name](self._store, args or {}, {"session_id": self._session_id,
                                                                  "platform": self._platform})
        except (KeyError, ValueError, TypeError) as exc:
            return tool_error(f"Invalid arguments: {exc}")
        except Exception as exc:
            logger.warning("LanceDB memory tool %s failed: %s", tool_name, exc)
            return tool_error(f"{tool_name} failed: {type(exc).__name__}: {exc}")


def register(ctx) -> None:
    ctx.register_memory_provider(LanceMemoryProvider(config=_load_plugin_config()))
