"""Inference-local patience and safe recovery of an entirely silent proxy stream."""

import logging

from agent.model_metadata import is_local_endpoint

logger = logging.getLogger("agent.chat_completion_helpers")


def uses_local_stream_grace(agent) -> bool:
    # A LiteLLM proxy may be on loopback or a private LAN without running inference there.
    # Keep actual local/custom inference defaults and endpoint discovery semantics unchanged.
    return (getattr(agent, "provider", "") != "litellm"
            and bool(getattr(agent, "base_url", None)) and is_local_endpoint(agent.base_url))


def _has_read_timeout_cause(error, *, owned_stale: bool) -> bool:
    import httpx

    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, httpx.ReadTimeout):
            return True
        if owned_stale and isinstance(error, (httpx.ReadError, httpx.RemoteProtocolError)):
            return True
        error = error.__cause__ or error.__context__
    return False


def silent_proxy_read_timeout(call, error) -> bool:
    """No provider chunk, visible reasoning/text, partial action, or displaced owner may replay."""
    if (getattr(call.agent, "provider", "") != "litellm"
            or getattr(error, "status_code", None)
            or getattr(call.agent, "_interrupt_requested", False)
            or call._request_cancelled["value"]
            or call.deltas_were_sent["yes"] or call.first_delta_fired["done"]
            or call.provider_tool_in_flight["yes"] or call.result.get("partial_tool_names")):
        return False
    diag = call.clients.diag or {}
    if diag.get("chunks") or diag.get("first_chunk_at") or call._route_switched_under_request():
        return False
    attempt = getattr(call, "stream_attempt_state", {}).get("current")
    owned_stale = attempt is not None and attempt in getattr(call, "_stale_counted_attempts", set())
    return _has_read_timeout_cause(error, owned_stale=owned_stale)


def _probe_allowed(call, error, silent_timeout):
    from agent import chat_completion_helpers as h

    if (getattr(call.agent, "_interrupt_requested", False) or call.deltas_were_sent["yes"]
            or getattr(call.agent, "api_mode", "") not in ("", "chat_completions")):
        return False
    status = h._extract_status_code(error)
    return silent_timeout or (status is not None and status >= 500)


def _record_probe_failure(call, error, silent_timeout):
    from agent import chat_completion_helpers as h

    status = h._extract_status_code(error)
    if silent_timeout or (status is not None and status < 500):
        call.result["error"] = error
        return True  # Propagate the real failure; do not open another identical stream.
    logger.info("Non-streaming unmask probe failed: %s", error)
    return False


def _recovery_was_displaced(call):
    return (call._request_cancelled["value"] or getattr(call.agent, "_interrupt_requested", False)
            or call._route_switched_under_request())


class StreamFallbackMixin:
    """Request-local recovery, sharing the stream owner's delivery and cancellation fences."""

    def _unmask_server_error_with_nonstreaming(self, e: Exception) -> bool:
        """One non-streaming re-issue for a pre-delta 5xx or entirely silent LiteLLM read timeout.

        Some gateways validate the request only on their non-streaming path and crash
        opaquely ("500 something went wrong") when streaming — the real 4xx, with its
        actionable message, never reaches the user through stream retries. One
        non-streaming probe per 60s window (timestamped on the agent: each outer retry
        builds a fresh _StreamingCall, so an instance flag would re-probe every attempt)
        surfaces it: a transient gateway 500 recovers this turn WITHOUT latching
        non-streaming; a silent proxy timeout switches this conversation after successful recovery.
        on a probe 4xx that error REPLACES the opaque 5xx. Other failed 5xx probes keep
        the original error; failed timeout recovery propagates its error without re-streaming.
        The successful delivery is bracketed by its own stream
        start/end pair (the failed attempt already emitted a terminal end), and a response
        that cannot be replayed propagates ``e``. The probe runs on this worker thread while
        the stream monitor still polls, so its stale check is suspended for the probe's
        duration (the probe has its own non-streaming watchdog). Interrupts re-raise (the outer handler routes them), and a /stop that arrived before
        this point suppresses the probe entirely — the loop's pre-retry interrupt check owns
        that decision, so a pending stop must not buy one more request.
        True = handled (caller must not overwrite result); False = propagate ``e``.
        """
        from agent import chat_completion_helpers as h

        silent_timeout = silent_proxy_read_timeout(self, e)
        if not _probe_allowed(self, e, silent_timeout):
            return False
        now = h.time.monotonic()
        last_probe = self.agent._stream_5xx_probe_ts
        if last_probe is not None and now - last_probe < h._STREAM_5XX_PROBE_WINDOW_S:
            return False  # one probe per 60s window
        self.agent._stream_5xx_probe_ts = now
        probe_kwargs = {k: v for k, v in self.api_kwargs.items() if k not in ("stream", "stream_options")}
        stale_timeout = self._stream_stale_timeout
        self._stream_stale_timeout = float("inf")  # no chunks arrive during the probe
        try:
            probe = h.interruptible_api_call(self.agent, probe_kwargs)
        except (KeyboardInterrupt, InterruptedError):
            raise  # the outer handler routes user interrupts; never swallow them
        except Exception as probe_err:
            logger.debug("Non-streaming recovery request failed", exc_info=True)
            return _record_probe_failure(self, probe_err, silent_timeout)
        finally:
            self._stream_stale_timeout = stale_timeout
        if silent_timeout and _recovery_was_displaced(self):
            raise InterruptedError("Stream recovery was interrupted or its model route changed")
        logger.info("Recovered %s without streaming for %s/%s",
                      "silent proxy timeout" if silent_timeout else "streaming server error",
                      self.agent.provider or "unknown", self.agent.model or "unknown")
        if not silent_timeout:
            self._quiet(self.agent._buffer_status,
                        "⚠  Streaming failed with a provider server error; the non-streaming retry succeeded.")
        try:
            # The failed attempt already emitted its terminal on_stream_end(finished=False),
            # so the recovered delivery opens and closes its OWN stream pair — consumers must
            # never see deltas after that error event.
            replayed = h._with_stream_emitters(self.agent, lambda: self._replay_final_response(probe))
        except (KeyboardInterrupt, InterruptedError):
            raise
        except Exception as replay_err:
            # A response we cannot replay must not escape into _call()'s except block.
            logger.exception("Non-streaming unmask probe response could not be replayed: %s", replay_err)
            return False
        self.result["response"] = replayed
        if silent_timeout:
            self.agent._disable_streaming = True
            self.agent._emit_warning(
                "⚠️ Streaming did not respond. Recovered without streaming; this conversation will continue without it.")
        return True
