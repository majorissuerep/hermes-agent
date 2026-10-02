"""Deterministic estimates for warm continuation versus a cold model switch."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from agent.usage_pricing import PricingEntry, get_pricing_entry, resolve_billing_route

_MILLION = Decimal(1000000)


def nonnegative_decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("Prices must be finite, nonnegative numbers")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Prices must be finite, nonnegative numbers") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("Prices must be finite, nonnegative numbers")
    return result


@dataclass(frozen=True)
class PoolModel:
    name: str
    provider: str
    model: str
    tier: int
    context_length: int
    base_url: str = ""
    api_mode: str = ""
    pricing: PricingEntry | None = None
    latency_ms: Decimal = Decimal(0)
    cache_min_tokens: int = 1024
    cache_ttl_seconds: int = 300
    supports_tools: bool = True
    supports_images: bool = False

    def matches(self, agent: Any) -> bool:
        from hermes_cli.models import normalize_provider
        providers = {normalize_provider(agent.provider), normalize_provider(getattr(agent, "requested_provider", agent.provider))}
        return (self.model == agent.model and normalize_provider(self.provider) in providers
                and (not self.base_url or self.base_url.rstrip("/") == agent.base_url.rstrip("/"))
                and (not self.api_mode or self.api_mode == agent.api_mode))


def _integer(value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"Expected an integer between {minimum} and {maximum}")
    return value


def parse_pool(rows: Any) -> list[PoolModel]:
    if not isinstance(rows, list) or len(rows) > 32:
        raise ValueError("smart_model_routing.models must be a list of at most 32 models")
    models = []
    names, routes = set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each routing pool entry must be a mapping")
        name, provider, model = (str(row.get(key) or "").strip() for key in ("name", "provider", "model"))
        if not all((name, provider, model)) or provider.lower() == "auto" or model.lower() == "auto":
            raise ValueError("Every pool entry needs a name, explicit provider and model")
        base_url = str(row.get("base_url") or "").rstrip("/")
        identity = provider, model, base_url
        if name in names or identity in routes:
            raise ValueError("Routing pool names and routes must be unique")
        names.add(name)
        routes.add(identity)
        prices = row.get("prices")
        if prices is not None:
            if not isinstance(prices, dict) or not {"input", "output"} <= prices.keys():
                raise ValueError("prices needs input and output USD rates per million tokens")
            rates = {key: nonnegative_decimal(value) for key, value in prices.items()}
            pricing = PricingEntry(
                input_cost_per_million=rates["input"], output_cost_per_million=rates["output"],
                cache_read_cost_per_million=rates.get("cache_read"),
                cache_write_cost_per_million=rates.get("cache_write"),
                request_cost=rates.get("request"), source="user_override",
            )
        else:
            pricing = None
        models.append(PoolModel(
            name, provider, model, _integer(row.get("tier"), 1, 3),
            _integer(row.get("context_length"), 1024, 10000000), base_url,
            str(row.get("api_mode") or ""), pricing,
            nonnegative_decimal(row.get("latency_ms", 0)),
            _integer(row.get("cache_min_tokens", 1024), 0, 10000000),
            _integer(row.get("cache_ttl_seconds", 300), 0, 86400),
            row.get("supports_tools", True) is True, row.get("supports_images", False) is True,
        ))
    return models


def resolve_prices(model: PoolModel, *, base_url: str = "", api_key: str = "") -> PricingEntry | None:
    if model.pricing is not None:
        return model.pricing
    route = resolve_billing_route(model.model, model.provider, model.base_url or base_url)
    if route.billing_mode == "subscription_included":
        return PricingEntry(Decimal(0), Decimal(0), source="none")
    return get_pricing_entry(model.model, provider=model.provider,
                             base_url=model.base_url or base_url, api_key=api_key)


def _call_cost(entry: PricingEntry, prompt: int, output: int, warm: int) -> Decimal | None:
    above = entry.tier_threshold_tokens is not None and prompt > entry.tier_threshold_tokens
    def rate(key: str):
        higher = getattr(entry, key + "_above", None) if above else None
        return higher if higher is not None else getattr(entry, key)

    inp, out = rate("input_cost_per_million"), rate("output_cost_per_million")
    if inp is None or out is None:
        return None
    read = rate("cache_read_cost_per_million")
    write = rate("cache_write_cost_per_million")
    # Newly appended input can incur cache creation on a warm continuation too.
    # Use the higher tariff for uncached input instead of understating either route.
    if write is not None:
        inp = max(inp, write)
    warm = min(prompt, max(0, warm)) if read is not None else 0
    cost = (Decimal(prompt - warm) * inp + Decimal(warm) * (read or Decimal(0)) + Decimal(output) * out) / _MILLION
    return cost + (entry.request_cost or Decimal(0))


@dataclass(frozen=True)
class RouteQuote:
    next_call_usd: Decimal | None
    cold_call_usd: Decimal | None
    horizon_usd: Decimal | None
    score: Decimal | None


def quote_route(model: PoolModel, entry: PricingEntry | None, *, prompt_tokens: int,
                output_tokens: int, remaining_calls: int, warm_prefix_tokens: int = 0,
                switching: bool = False, latency_cost_per_second: Decimal = Decimal(0)) -> RouteQuote:
    if entry is None:
        return RouteQuote(None, None, None, None)
    cold = _call_cost(entry, prompt_tokens, output_tokens, 0)
    first = cold if switching else _call_cost(entry, prompt_tokens, output_tokens, warm_prefix_tokens)
    if first is None:
        return RouteQuote(None, cold, None, None)
    total = first
    # Project subsequent requests on the same destination as warm only when it has a
    # published cache tariff. The report labels this projection; only the next call's
    # existing prefix is based on observed cache usage.
    can_cache = (entry.cache_read_cost_per_million is not None and model.cache_ttl_seconds > 0
                 and prompt_tokens >= model.cache_min_tokens)
    for index in range(1, remaining_calls):
        prompt = prompt_tokens + index * output_tokens
        warm = prompt_tokens + (index - 1) * output_tokens if can_cache else 0
        cost = _call_cost(entry, prompt, output_tokens, warm)
        if cost is None:
            return RouteQuote(first, cold, None, None)
        total += cost
    score = total + latency_cost_per_second * model.latency_ms / 1000 * remaining_calls
    return RouteQuote(first, cold, total, score)
