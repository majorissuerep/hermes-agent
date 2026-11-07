"""The compaction summary request: dispatch, thinking-exhaustion recovery, and response validation.

A thinking summarizer behind a relay with a fixed output ceiling can spend that whole ceiling on
reasoning and stop with ``finish_reason=length`` before writing any summary. A LiteLLM gateway fronting
Qwen3.8 applies the model's ``max_completion_tokens`` (12,288) when the request omits one, which a
generated summary always does (#60989). That is not a summary that ran long: the identical request
re-burns the identical budget. Observed: every attempt spent ~225 s producing ~49K chars of reasoning and
no summary, and the cooldown ladder then held compression off until the transcript overflowed the window.

The main loop already answers a thinking-only truncation with a one-shot reasoning-off continuation
(``agent.turn_truncation``). The summary call applies the same remedy: ONE re-issue of the same request
with the route's thinking-off encoding (``reasoning_effort: "none"`` on custom endpoints; a route that
refuses the disable is stepped up to the ``low`` floor by the auxiliary ladder). The first request keeps
the historic shape (auto and inherited routes deliberately carry no reasoning override; thinking-off up
front is the opt-in compression fast lane) until a response proves the exhaustion. The compressor then
remembers that proof for the route it requested, so later compactions in the session skip the wasted
ceiling-length call.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from agent.auxiliary_client import _coerce_llm_message, _message_field, extract_content_or_reasoning

logger = logging.getLogger(__name__)

# Same shape as the main loop's one-shot override (``_reasoning_config_for_wire``).
_REASONING_OFF = {"enabled": False, "effort": "none"}

_ROUTE_KEY_FIELDS = ("provider", "model", "base_url", "api_mode")


def _cc():
    """The facade module, read at call time so its patched seams stay the ones production uses."""
    from agent import context_compressor

    return context_compressor


def summary_reasoning_exhausted(response: Any) -> bool:
    """A ``length`` stop whose message carried reasoning: thinking shared the capped output budget and
    ran it out. A length stop without reasoning is a summary that genuinely ran long — not this case."""
    if _cc()._response_finish_reason(response) != "length":
        return False
    message = _coerce_llm_message(response)
    if message is None:
        return False
    if not isinstance(message, str):
        if any(
            isinstance(value, str) and value.strip()
            for value in (_message_field(message, "reasoning"), _message_field(message, "reasoning_content"))
        ):
            return True
        details = _message_field(message, "reasoning_details")
        if isinstance(details, list) and details:
            return True
        message = _message_field(message, "content")
    if not isinstance(message, str) or not message.strip():
        return False
    # Inline thinking (``<think>…``, usually unterminated at a length stop).
    from agent.agent_runtime_helpers import strip_think_blocks

    return strip_think_blocks(None, message).strip() != message.strip()


def _task_disables_reasoning() -> bool:
    """``auxiliary.compression`` already sends thinking off (``reasoning_effort: none`` or an explicit
    ``extra_body.reasoning``), so a reasoning-off retry would repeat the identical request."""
    from agent.auxiliary_client import _get_task_extra_body

    reasoning = _get_task_extra_body("compression").get("reasoning")
    return isinstance(reasoning, dict) and reasoning.get("enabled") is False


def _summary_route_key(call_kwargs: dict[str, Any]) -> tuple:
    """Everything the compressor itself chose about the summary route: explicit route fields plus the
    main runtime the aux resolver inherits from. A model switch or route pin yields a different key."""
    main = call_kwargs.get("main_runtime") or {}
    return tuple(call_kwargs.get(field) for field in _ROUTE_KEY_FIELDS) + tuple(
        main.get(field) for field in _ROUTE_KEY_FIELDS
    )


def _route_label(aux_route: dict[str, str], compressor: Any) -> str:
    """The route that actually answered (``call_llm`` records it), else the compressor's own."""
    provider = aux_route.get("provider") or compressor.provider or "auto"
    model = aux_route.get("model") or compressor.summary_model or compressor.model
    return f"(provider={provider} model={model})"


class SummaryCallMixin:
    # Route key whose summaries proved they exhaust the output cap with reasoning on.
    _summary_reasoning_off_route: Optional[tuple] = None

    def _call_summary_llm(self, prompt: str, prompt_started_at: float) -> str:
        """Issue the aux summary request; return validated content text.

        One recovery: a thinking-exhausted response is re-issued once with reasoning off. Raises
        RuntimeError for empty, refused, or length-truncated (PARTIAL) content so the failure routes
        through main-model fallback + cooldown instead of wiping the compacted turns."""
        from agent.context_chunking import selection_call_kwargs

        call_kwargs: dict[str, Any] = {
            "task": "compression",
            "main_runtime": {
                "model": self.model, "provider": self.provider, "base_url": self.base_url, "api_key": self.api_key,
                "api_mode": self.api_mode,
            },
            # call_llm writes the route it actually selected; never pre-resolve a second, stale pair.
            "messages": [{"role": "user", "content": prompt}], "route_info": {},
            # Only range selection reserves output; generated summaries stay uncapped (Anthropic/NIM wires
            # forward a cap and thinking models burn it on reasoning). Timeout comes from call_llm config.
            **selection_call_kwargs(),
        }
        # Pinned route (stall fallback) replaces task routing so the retry leaves the stalled backend.
        self._apply_summary_route(call_kwargs, _cc()._pinned_summary_call_kwargs())
        route_key = _summary_route_key(call_kwargs)
        remembered = self._summary_reasoning_off_route == route_key
        if remembered:
            call_kwargs["reasoning_config"] = dict(_REASONING_OFF)
            logger.info("Compression summary: reasoning off (this route exhausted its output cap on reasoning)")
        response = self._dispatch_summary_llm(call_kwargs, prompt_started_at)
        reasoning_off = remembered or _task_disables_reasoning()
        if not reasoning_off and summary_reasoning_exhausted(response):
            logger.warning(
                "Compression summary: reasoning consumed the output token cap (finish_reason=length) %s; "
                "retrying once with reasoning off", _route_label(call_kwargs["route_info"], self),
            )
            from agent.conversation_compression import _raise_if_stale_attempt

            # A superseded attempt must not spend another provider call.
            _raise_if_stale_attempt(self)
            self._summary_reasoning_off_route = route_key
            reasoning_off = True
            call_kwargs = {**call_kwargs, "reasoning_config": dict(_REASONING_OFF), "route_info": {}}
            response = self._dispatch_summary_llm(call_kwargs, time.monotonic())
        return self._validated_summary_content(response, call_kwargs["route_info"], reasoning_off)

    def _validated_summary_content(self, response: Any, aux_route: dict[str, str], reasoning_off: bool) -> str:
        """Content text of a usable summary response; RuntimeError for empty, refused, or truncated."""
        # Reasoning-field fallback (DeepSeek/Qwen/Kimi put the summary in reasoning_content); capped.
        content = extract_content_or_reasoning(response, max_reasoning_chars=8000)
        where = _route_label(aux_route, self)
        # Some OpenAI-compatible proxies (e.g. cmkey.cn, one-api channels) return a well-formed HTTP 200
        # with an empty or whitespace-only ``content`` instead of an error or empty ``choices``. Stored, it
        # would be a prefix-only summary that silently wipes the compacted turns (#11978, #11914), so it
        # routes through the same main-model fallback + cooldown machinery as a transport error.
        if not content.strip():
            raise RuntimeError(f"Context compression LLM returned empty content {where}")
        if _cc()._is_refusal_response(response, content):
            # A refusal reuses the empty-body path, so it can never be committed as `_previous_summary`.
            raise RuntimeError(f"Context compression LLM returned refusal content {where}")
        # finish_reason=length: the text is PARTIAL. As a checkpoint it would replace the real middle turns
        # and feed the cut-off text into every later iterative update, compounding the loss. (pi#7048.)
        if _cc()._response_finish_reason(response) == "length":
            marker = _cc()._TRUNCATED_SUMMARY_MARKER
            if reasoning_off and summary_reasoning_exhausted(response):
                # Cause and remedy ahead of the route: the user-facing copy is clipped to ~220 chars.
                raise RuntimeError(
                    f"Compression summary truncated ({marker}): reasoning filled the output cap even with thinking "
                    f"off; set the endpoint's thinking control in auxiliary.compression.extra_body {where}"
                )
            raise RuntimeError(
                f"Context compression summary was truncated ({marker}): generation hit "
                f"the output token cap and the summary is incomplete {where}"
            )
        return content
