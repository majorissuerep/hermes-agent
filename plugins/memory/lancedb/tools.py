"""Model-facing tools: ``lance_recall`` (search) and ``lance_memory`` (write + navigate).

Two tools, not ten: every schema ships on every API call. Navigation is an ``action`` enum on
``lance_memory`` so the model can walk from a search hit to its session, its neighbours in time,
or semantically related memories without new top-level surface.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from tools.registry import tool_error

from .store import KINDS, MemoryStore

_LIST_TEXT_CHARS = 500
_MAX_LIMIT = 50

_FILTER_PROPS = {
    "kinds": {"type": "array", "items": {"type": "string", "enum": list(KINDS)},
              "description": "Restrict to kinds: fact (durable, explicitly remembered), turn (a past exchange), "
                             "delegation (a subagent task + result)."},
    "session_id": {"type": "string", "description": "Restrict to one session."},
    "since": {"type": "string", "description": "Only memories created at/after this: ISO date/datetime, or relative like '7d', '12h'."},
    "until": {"type": "string", "description": "Only memories created before this (same formats as since)."},
    "tags": {"type": "array", "items": {"type": "string"}, "description": "Require ALL of these tags."},
    "limit": {"type": "integer", "description": f"Max results (default 8, max {_MAX_LIMIT})."},
}

RECALL_SCHEMA = {
    "name": "lance_recall",
    "description": (
        "Search long-term memory across ALL past sessions: durable facts plus past conversation turns. "
        "Use before answering anything that may depend on earlier work, the user's preferences, people, "
        "projects or decisions — the chat window is not all you know. Returns ids you can open with "
        "lance_memory (get / related / session). For multi-part questions search several times with "
        "different wording."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to look for, in natural language or keywords."},
            "mode": {"type": "string", "enum": ["hybrid", "semantic", "keyword"],
                     "description": "hybrid (default) fuses meaning + exact keywords; keyword for exact names/ids/errors."},
            **_FILTER_PROPS,
        },
        "required": ["query"],
    },
}

MEMORY_SCHEMA = {
    "name": "lance_memory",
    "description": (
        "Write and navigate long-term memory.\n"
        "• remember — store a durable fact (text, optional tags) the moment the user states a lasting "
        "preference, decision, correction or personal detail.\n"
        "• update — correct a memory in place (id + new text and/or tags) instead of adding a duplicate.\n"
        "• forget — permanently erase memories (id or ids); the encrypted log is scrubbed.\n"
        "• get — one memory in full, with the previous/next turn of its session.\n"
        "• related — memories semantically close to an id.\n"
        "• timeline — browse newest-first with filters (kinds/session_id/since/until/tags), paged by offset.\n"
        "• sessions — past sessions with time span, turn count and opening message.\n"
        "• session — replay one session's memories in order (default: the current session).\n"
        "• stats — counts by kind, sessions, date range, embedding status."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["remember", "update", "forget", "get", "related", "timeline",
                                                   "sessions", "session", "stats"]},
            "text": {"type": "string", "description": "Memory text (remember / update)."},
            "id": {"type": "string", "description": "Memory id (update / forget / get / related)."},
            "ids": {"type": "array", "items": {"type": "string"}, "description": "Several ids (forget)."},
            "offset": {"type": "integer", "description": "Skip this many results (timeline / sessions / session)."},
            **_FILTER_PROPS,
        },
        "required": ["action"],
    },
}

TOOL_SCHEMAS = [RECALL_SCHEMA, MEMORY_SCHEMA]

_RELATIVE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([mhdw])\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_time(value: Any) -> Optional[float]:
    """ISO date/datetime (naive = UTC) or a relative age like ``7d`` → epoch seconds."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if m := _RELATIVE_RE.match(text):
        return time.time() - float(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()]
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"unrecognized time {value!r}: use ISO (2026-09-01) or relative (7d, 12h)") from exc
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def _when(ts: Optional[float]) -> str:
    return datetime.fromtimestamp(ts or 0, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def present(rec: Dict[str, Any], *, full: bool = False) -> Dict[str, Any]:
    text = rec.get("text") or ""
    out = {"id": rec["id"], "kind": rec["kind"], "when": _when(rec.get("created_at")),
           "text": text if full or len(text) <= _LIST_TEXT_CHARS else text[:_LIST_TEXT_CHARS] + "…"}
    for key in ("session_id", "platform", "source"):
        if rec.get(key):
            out[key] = rec[key]
    if rec.get("tags"):
        out["tags"] = list(rec["tags"])
    if rec.get("updated_at") and rec.get("created_at") and rec["updated_at"] - rec["created_at"] > 1:
        out["updated"] = _when(rec["updated_at"])
    for key in ("score", "similarity", "keyword_score"):
        if key in rec:
            out[key] = rec[key]
    return out


def _limit(args: Dict[str, Any], default: int = 8) -> int:
    return max(1, min(int(args.get("limit") or default), _MAX_LIMIT))


def _offset(args: Dict[str, Any]) -> int:
    return max(0, int(args.get("offset") or 0))


def _filters(args: Dict[str, Any]) -> Dict[str, Any]:
    kinds = [k for k in (args.get("kinds") or []) if k in KINDS]
    return {"kinds": kinds, "session_id": str(args.get("session_id") or ""),
            "since": parse_time(args.get("since")), "until": parse_time(args.get("until")),
            "tags": [str(t) for t in (args.get("tags") or []) if str(t).strip()]}


def _matches(rec: Dict[str, Any], f: Dict[str, Any]) -> bool:
    ts = rec.get("created_at") or 0
    return ((not f["kinds"] or rec["kind"] in f["kinds"])
            and (not f["session_id"] or rec.get("session_id") == f["session_id"])
            and (f["since"] is None or ts >= f["since"])
            and (f["until"] is None or ts < f["until"])
            and all(t in (rec.get("tags") or []) for t in f["tags"]))


def _page(items: List[Any], args: Dict[str, Any], key: str, *, default_limit: int = 8, full: bool = False) -> str:
    offset, limit = _offset(args), _limit(args, default_limit)
    page = items[offset:offset + limit]
    body = {key: [present(r, full=full) if isinstance(r, dict) and "kind" in r else r for r in page],
            "count": len(page), "total": len(items)}
    if offset + limit < len(items):
        body["next_offset"] = offset + limit
    return json.dumps(body, ensure_ascii=False)


# -- handlers: (store, args, ctx) -> JSON string; ctx = {"session_id", "platform"} ---------


def recall(store: MemoryStore, args: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return tool_error("Missing required parameter: query")
    mode = args.get("mode") if args.get("mode") in ("hybrid", "semantic", "keyword") else "hybrid"
    results = store.search(query, mode=mode, limit=_limit(args), where=store.build_where(**_filters(args)))
    body: Dict[str, Any] = {"results": [present(r) for r in results], "count": len(results)}
    if mode != "keyword" and not store.embedder.ready:
        body["note"] = "embedding model not loaded yet: results are keyword-only"
    return json.dumps(body, ensure_ascii=False)


def _remember(store, args, ctx):
    text = str(args.get("text") or "").strip()
    if not text:
        return tool_error("remember requires 'text'")
    rec = store.put(kind="fact", text=text, tags=args.get("tags") or [], source="tool",
                    session_id=ctx.get("session_id", ""), platform=ctx.get("platform", ""))
    return json.dumps({"status": "remembered", "memory": present(rec, full=True)}, ensure_ascii=False)


def _update(store, args, ctx):
    rec = store.get(str(args.get("id") or ""))
    if rec is None:
        return tool_error(f"Memory not found: {args.get('id')}")
    text = str(args.get("text") or "").strip() or rec["text"]
    tags = args.get("tags") if args.get("tags") is not None else None
    updated = store.put(kind=rec["kind"], text=text, tags=tags, record_id=rec["id"])
    return json.dumps({"status": "updated", "memory": present(updated, full=True)}, ensure_ascii=False)


def _forget(store, args, ctx):
    ids = [str(i) for i in (args.get("ids") or [])] + ([str(args["id"])] if args.get("id") else [])
    if not ids:
        return tool_error("forget requires 'id' or 'ids'")
    erased = store.erase(ids)
    return json.dumps({"erased": erased, "not_found": [i for i in ids if i not in erased]})


def _session_records(store: MemoryStore, session_id: str) -> List[Dict[str, Any]]:
    return sorted((r for r in store.records() if r.get("session_id") == session_id), key=lambda r: r["created_at"])


def _get(store, args, ctx):
    rec = store.get(str(args.get("id") or ""))
    if rec is None:
        return tool_error(f"Memory not found: {args.get('id')}")
    body: Dict[str, Any] = {"memory": present(rec, full=True)}
    if rec.get("session_id"):
        turns = [r for r in _session_records(store, rec["session_id"]) if r["kind"] == "turn"]
        ids = [r["id"] for r in turns]
        if rec["id"] in ids:
            i = ids.index(rec["id"])
            body["previous_turn"] = present(turns[i - 1]) if i > 0 else None
            body["next_turn"] = present(turns[i + 1]) if i + 1 < len(turns) else None
            body["position"] = f"turn {i + 1} of {len(turns)} in session"
    return json.dumps(body, ensure_ascii=False)


def _related(store, args, ctx):
    rid = str(args.get("id") or "")
    if store.get(rid) is None:
        return tool_error(f"Memory not found: {rid}")
    vector = store.vector_of(rid)
    if vector is None:
        return json.dumps({"results": [], "count": 0, "note": "this memory has no embedding yet"})
    f = _filters(args)
    rows = store.vector_search(vector, limit=_limit(args), where=store.build_where(**f, exclude_ids=[rid]))
    return json.dumps({"results": [present(r) for r in rows], "count": len(rows)}, ensure_ascii=False)


def _timeline(store, args, ctx):
    f = _filters(args)
    items = sorted((r for r in store.records() if _matches(r, f)), key=lambda r: r["created_at"], reverse=True)
    return _page(items, args, "memories")


def _sessions(store, args, ctx):
    f = _filters(args)
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for rec in store.records():
        if rec.get("session_id") and _matches(rec, {**f, "kinds": [], "session_id": ""}):
            groups[rec["session_id"]].append(rec)
    rows = []
    for sid, recs in groups.items():
        recs.sort(key=lambda r: r["created_at"])
        turns = [r for r in recs if r["kind"] == "turn"]
        opener = (turns[0]["text"] if turns else recs[0]["text"]).split("\nAssistant:", 1)[0]
        rows.append({"session_id": sid, "started": _when(recs[0]["created_at"]), "last": _when(recs[-1]["created_at"]),
                     "turns": len(turns), "memories": len(recs), "platform": recs[0].get("platform") or "",
                     "current": sid == ctx.get("session_id"), "opening": opener[:200],
                     "_last": recs[-1]["created_at"]})
    rows.sort(key=lambda r: r.pop("_last"), reverse=True)
    return _page(rows, args, "sessions", default_limit=10)


def _session(store, args, ctx):
    sid = str(args.get("session_id") or ctx.get("session_id") or "")
    if not sid:
        return tool_error("session requires 'session_id'")
    f = {**_filters(args), "session_id": sid}
    items = [r for r in _session_records(store, sid) if _matches(r, f)]
    return _page(items, args, "memories", default_limit=20)


def _stats(store, args, ctx):
    recs = store.records()
    by_kind: Dict[str, int] = defaultdict(int)
    for r in recs:
        by_kind[r["kind"]] += 1
    stamps = [r["created_at"] for r in recs]
    tags: Dict[str, int] = defaultdict(int)
    for r in recs:
        for t in r.get("tags") or []:
            tags[t] += 1
    return json.dumps({
        "total": len(recs), "by_kind": dict(by_kind),
        "sessions": len({r["session_id"] for r in recs if r.get("session_id")}),
        "oldest": _when(min(stamps)) if stamps else None, "newest": _when(max(stamps)) if stamps else None,
        "top_tags": dict(sorted(tags.items(), key=lambda kv: -kv[1])[:20]),
        "embedding_model": store.embedder.model_name,
        "embedding_status": "ready" if store.embedder.ready else (store.embedder.error or "loading"),
        "without_embedding": sum(1 for r in recs if not r["has_vector"]),
        "storage": "vault-encrypted frame log; in-memory LanceDB index",
    }, ensure_ascii=False)


_MEMORY_ACTIONS: Dict[str, Callable[[MemoryStore, Dict[str, Any], Dict[str, Any]], str]] = {
    "remember": _remember, "update": _update, "forget": _forget, "get": _get, "related": _related,
    "timeline": _timeline, "sessions": _sessions, "session": _session, "stats": _stats,
}


def memory(store: MemoryStore, args: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    action = args.get("action")
    if action not in _MEMORY_ACTIONS:
        return tool_error(f"Unknown action: {action}")
    return _MEMORY_ACTIONS[action](store, args, ctx)


HANDLERS = {"lance_recall": recall, "lance_memory": memory}
