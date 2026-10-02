"""Opt-in policy review and model routing defaults."""

GUARD_DEFAULTS = {
    "enabled": False,
    "policy": "",
    "max_context_chars": 12000,
}

ROUTING_DEFAULTS = {
    "enabled": False,
    "models": [],
    "max_context_chars": 48000,
    "min_calls_before_switch": 3,
    "min_savings_ratio": 0.15,
    "failure_threshold": 2,
    # USD-equivalent penalty per second: zero minimizes the provider bill.
    "latency_cost_per_second": 0.0,
}
