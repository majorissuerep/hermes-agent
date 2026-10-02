"""Bounded, pinned auxiliary calls for policy and routing decisions.

Use the normal client/relay boundary so profile credentials, usage and observer hooks
still apply. These decisions deliberately make one attempt at the configured route:
a dead local endpoint must not export the workspace or incur a cloud fallback bill.
"""

from __future__ import annotations

import json
from typing import Any


def text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def recent_history(messages: list, limit: int = 12) -> list[dict]:
    history = []
    for message in messages[-limit:]:
        if message.get("role") == "system":
            continue
        text = text_content(message.get("content"))
        row = {"role": message.get("role"), "content": text[:2000]}
        if len(text) > 2000:
            row["truncated"] = True
        if message.get("tool_calls"):
            # Arguments stay in the separate, untruncated proposed-call field on guard requests.
            row["tools"] = [call.get("function", {}).get("name") for call in message["tool_calls"]]
        history.append(row)
    return history


def bounded_packet(packet: dict, max_chars: int) -> str:
    """Drop old evidence first; never truncate the proposed operation or authorized task."""
    packet = dict(packet)
    history = list(packet.get("history") or [])
    packet["history"] = history
    while True:
        encoded = json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) <= max_chars:
            return encoded
        if not history:
            raise ValueError("Sidecar context limit exceeded; the task and operation cannot be reviewed in full")
        history.pop(0)
        packet["history_truncated"] = True


def call_decision(task: str, config: dict, system: str, packet: dict, schema: dict, max_chars: int) -> dict:
    from agent import auxiliary_client as aux

    provider = str(config.get("provider") or "").strip()
    model = str(config.get("model") or "").strip()
    if not provider or provider.lower() == "auto" or not model or model.lower() == "auto":
        raise ValueError(f"Pin auxiliary.{task}.provider and .model before enabling this sidecar")
    base_url = config.get("base_url") or None
    api_key = config.get("api_key") or None
    from hermes_cli.local_runtime.endpoint import LLAMACPP_ALIASES, resolve_llamacpp_endpoint
    if provider.lower() in LLAMACPP_ALIASES and not base_url:
        endpoint = resolve_llamacpp_endpoint()
        if endpoint is None:
            raise ValueError("The configured local sidecar server is unavailable")
        base_url, api_key = endpoint["base_url"], endpoint.get("api_key") or "local"
    timeout = float(config.get("timeout", 15))
    if not 0 < timeout <= 120:
        raise ValueError(f"auxiliary.{task}.timeout must be between 0 and 120 seconds")
    content = bounded_packet(packet, max_chars - len(system))
    messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
    extra_body = dict(config.get("extra_body") or {})
    extra_body["response_format"] = {
        "type": "json_schema",
        "json_schema": {"name": task, "strict": True, "schema": schema},
    }
    # _prepare_aux_request applies the task's normal reasoning/endpoint configuration.
    # Its relay scope owns logical completion, including failures during client resolution.
    with aux._relay_aux_call_scope((), {"task": task}):
        request = aux._prepare_aux_request(
            task, provider=provider, model=model, base_url=base_url,
            api_key=api_key, main_runtime={}, messages=messages,
            temperature=0, max_tokens=256, tools=None, timeout=timeout, extra_body=extra_body,
            reasoning_config=None, extra_headers=None, api_mode=config.get("api_mode") or None,
            route_info=None, async_mode=False,
        )
        response = aux._relay_sync_completion(
            request.client, request.kwargs, provider=request.request_provider,
            api_mode=request.resolved_api_mode,
            create=lambda kwargs: aux._create_with_progress(
                request.client, kwargs, task,
                force_stream=aux._provider_requires_stream(request.request_provider, request.base_info),
            ),
        )
        raw = response.choices[0].message.content or ""
        if not isinstance(raw, str) or len(raw) > 4096:
            raise ValueError("Invalid sidecar response")
        result = json.loads(raw)
        if not isinstance(result, dict) or set(result) != set(schema["required"]):
            raise ValueError("Sidecar response does not match the decision schema")
        aux._complete_relay_auxiliary_call()
        return result
