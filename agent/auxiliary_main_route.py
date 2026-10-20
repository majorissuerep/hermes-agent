"""Resolve explicit ``main`` as a connection identity, not a provider alias."""
from contextvars import ContextVar
from functools import wraps
from inspect import signature

_MAIN_HEADERS_ALLOWED = ContextVar("auxiliary_main_headers_allowed", default=True)


def main_headers_allowed():
    return _MAIN_HEADERS_ALLOWED.get()


def scoped_main_headers(function):
    """Foreign task construction must not inherit the main connection's model-level headers."""
    shape = signature(function)
    @wraps(function)
    def scoped(*args, **kwargs):
        from agent import auxiliary_client as aux
        from hermes_cli.route_identity import normalize_route_base_url
        request = shape.bind(*args, **kwargs).arguments
        runtime = aux._normalize_main_runtime(request.get("main_runtime"))
        base = request.get("explicit_base_url")
        foreign = (str(request.get("provider", "")).strip().lower() == "main" and base and
                   normalize_route_base_url(base) != normalize_route_base_url(runtime.get("base_url")))
        token = _MAIN_HEADERS_ALLOWED.set(_MAIN_HEADERS_ALLOWED.get() and not foreign)
        try:
            return function(*args, **kwargs)
        finally:
            _MAIN_HEADERS_ALLOWED.reset(token)
    return scoped


def inherit_main_route(provider, model, base_url, api_key, api_mode, main_runtime):
    from agent import auxiliary_client as aux
    aux._validate_proxy_env_urls()
    if str(provider or "").strip().lower() != "main":
        return provider, model, base_url, api_key, api_mode
    runtime = aux._normalize_main_runtime(main_runtime)
    provider = runtime.get("provider") or aux._read_main_provider() or "custom"
    live_base = runtime.get("base_url")
    from hermes_cli.route_identity import normalize_route_base_url
    same_endpoint = not base_url or normalize_route_base_url(base_url) == normalize_route_base_url(live_base)
    if not same_endpoint:
        if not api_key:
            raise aux.AuxiliaryClientUnavailable("A different main auxiliary endpoint requires an explicit credential")
        # A new task connection must not borrow the old named entry's auth headers either.
        provider = "custom"
    return (provider, model or runtime.get("model"), base_url or live_base,
            api_key if api_key is not None else runtime.get("api_key") if same_endpoint else None,
            api_mode or (runtime.get("api_mode") if same_endpoint else None))


def main_vision_route(model, base_url, api_key, api_mode, runtime, async_mode):
    from agent import auxiliary_client as aux

    client, model = aux.resolve_provider_client("main", model, async_mode, explicit_base_url=base_url,
        explicit_api_key=api_key, api_mode=api_mode, main_runtime=runtime, is_vision=True, task="vision")
    return runtime.get("provider") or aux._read_main_provider(), client, model if client is not None else None


def explicit_or_pool_key(provider, api_key):
    """A main sentinel owns the live key; independent auxiliary routes may select a pool entry."""
    from agent import auxiliary_client as aux

    if api_key or str(provider).strip().lower() == "main":
        return api_key
    entry = aux._peek_pool_entry(aux._normalize_aux_provider(provider))
    return (aux._pool_runtime_api_key(entry) or api_key) if entry is not None else api_key


def request_provider(provider, model, base_url, api_key, api_mode, runtime):
    """Project wire policy from the active connection while caching its main sentinel unchanged."""
    if str(provider or "").strip().lower() == "main":
        return inherit_main_route(provider, model, base_url, api_key, api_mode, runtime)[0]
    return provider
