"""A relay's measured prompt/output split must drive bounded overflow recovery."""
from types import SimpleNamespace
from unittest.mock import patch

from agent.model_metadata import (
    get_context_length_from_provider_error,
    is_output_cap_error,
    parse_available_output_tokens_from_error,
    parse_context_limit_from_error,
)


ERROR = (
    "request exceeds the context window: prompt (250468 tokens) + max tokens (12288) "
    "exceeds the context (262144); requests are never truncated. Send a smaller "
    'max_tokens (at most 11668 here), or add "fit_max_tokens": true to the model config'
)


def test_relay_explicit_context_and_available_output_are_recognized_separately():
    assert parse_context_limit_from_error(ERROR) == 262144
    assert parse_available_output_tokens_from_error(ERROR) == 11668
    assert get_context_length_from_provider_error(ERROR, 262144) is None
    assert get_context_length_from_provider_error(ERROR, 1000000) == 262144


def test_input_alone_over_window_is_not_mistaken_for_an_output_cap():
    overflow = ERROR.replace("250468 tokens", "300000 tokens").replace("11668 here", "-37864 here")
    assert parse_context_limit_from_error(overflow) == 262144
    assert parse_available_output_tokens_from_error(overflow) is None
    assert not is_output_cap_error(overflow)


def test_actual_overflow_handler_retries_with_a_fitting_output_cap_without_shrinking_the_window():
    from agent.error_classifier import classify_api_error
    from agent.turn_overflow import recover_from_overflow

    notices = []
    agent = SimpleNamespace(context_compressor=SimpleNamespace(context_length=262144),
                            _buffer_vprint=notices.append, log_prefix="")
    retry = SimpleNamespace(restart_with_compressed_messages=False)
    error = ValueError(ERROR)
    with patch("agent.turn_overflow._Recovery.request_tokens", return_value=250468), patch(
        "agent.turn_overflow._Recovery.compress_scored_by_tokens", return_value=(None, False, 250468),
    ):
        verdict = recover_from_overflow(
            agent, error, classify_api_error(error), retry, status_code=400, error_msg=ERROR,
            wrapped_output_cap_budget=None, messages=[], api_messages=[], system_message="system",
            active_system_prompt="system", conversation_history=[], approx_tokens=250468,
            compression_attempts=0, max_compression_attempts=3, api_call_count=1, effective_task_id=None,
        )
    assert verdict.action == "break" and verdict.compression_attempts == 1
    assert retry.restart_with_compressed_messages
    assert 0 < agent._ephemeral_max_output_tokens <= 11668
    assert 250468 + agent._ephemeral_max_output_tokens < 262144
    assert agent.context_compressor.context_length == 262144
