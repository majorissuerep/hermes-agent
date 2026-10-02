"""Warm continuity, cold switch and provider tariff contracts."""

from decimal import Decimal

from agent.model_router_costs import PoolModel, quote_route
from agent.usage_pricing import PricingEntry


def test_a_cheaper_input_tariff_can_cost_more_than_a_warm_continuation():
    current = PoolModel("current", "custom", "main", 2, 200000)
    other = PoolModel("fast", "custom", "fast", 1, 200000)
    expensive = PricingEntry(Decimal(5), Decimal(10), Decimal("0.1"), Decimal("6.25"))
    cheap = PricingEntry(Decimal(1), Decimal(2), Decimal("0.1"), Decimal("1.25"))
    stay = quote_route(current, expensive, prompt_tokens=100000, output_tokens=100,
                       remaining_calls=1, warm_prefix_tokens=99000)
    switch = quote_route(other, cheap, prompt_tokens=100000, output_tokens=100,
                         remaining_calls=1, switching=True)
    assert stay.next_call_usd < switch.next_call_usd
    assert switch.next_call_usd == switch.cold_call_usd
    assert stay.next_call_usd < stay.cold_call_usd
    # New input on the warm call still pays a published cache-creation premium.
    input_only = quote_route(current, PricingEntry(Decimal(5), Decimal(10), Decimal("0.1")),
                             prompt_tokens=100000, output_tokens=100, remaining_calls=1, warm_prefix_tokens=99000)
    assert stay.next_call_usd - input_only.next_call_usd == Decimal(1000) * (Decimal("6.25") - Decimal(5)) / 1000000
    # Over enough calls the lower output tariff pays for the cold first request.
    long_stay = quote_route(current, expensive, prompt_tokens=100000, output_tokens=4096,
                            remaining_calls=8, warm_prefix_tokens=99000)
    long_switch = quote_route(other, cheap, prompt_tokens=100000, output_tokens=4096,
                              remaining_calls=8, switching=True)
    assert long_switch.horizon_usd < long_stay.horizon_usd


def test_unknown_cache_tariffs_context_tiers_and_latency_are_priced_conservatively():
    model = PoolModel("main", "custom", "main", 2, 200000, latency_ms=Decimal(2000))
    tariff = PricingEntry(Decimal(1), Decimal(2), tier_threshold_tokens=100000,
                          input_cost_per_million_above=Decimal(2), output_cost_per_million_above=Decimal(4))
    base = quote_route(model, tariff, prompt_tokens=100000, output_tokens=100, remaining_calls=1)
    tiered = quote_route(model, tariff, prompt_tokens=100001, output_tokens=100,
                         remaining_calls=1, warm_prefix_tokens=100000)
    assert tiered.next_call_usd > 2 * base.next_call_usd
    # An absent cache tariff cannot make input tokens free.
    uncached = quote_route(model, tariff, prompt_tokens=100001, output_tokens=100, remaining_calls=1)
    assert tiered.next_call_usd == uncached.next_call_usd
    latency = quote_route(model, tariff, prompt_tokens=100000, output_tokens=100,
                          remaining_calls=1, latency_cost_per_second=Decimal("0.01"))
    assert latency.score - latency.horizon_usd == Decimal("0.02")
    assert quote_route(model, None, prompt_tokens=100000, output_tokens=100, remaining_calls=1).score is None
