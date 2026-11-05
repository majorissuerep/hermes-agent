"""Source selection reserves its output on custom relays without changing other calls."""
from agent.auxiliary_client import _build_call_kwargs
from agent.context_chunking import selection_output_reservation


def test_range_selection_output_cap_reaches_the_actual_custom_wire_builder_only_in_scope():
    arguments = dict(provider="openai", model="synthetic-relay", task="compression", max_tokens=4096,
                     base_url="https://relay.invalid/v1", messages=[{"role": "user", "content": "select"}])
    before = _build_call_kwargs(**arguments)
    with selection_output_reservation(4096):
        selected = _build_call_kwargs(**arguments)
    after = _build_call_kwargs(**arguments)
    assert "max_tokens" not in before and "max_completion_tokens" not in before
    assert selected.get("max_tokens", selected.get("max_completion_tokens")) == 4096
    assert after == before
