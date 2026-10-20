"""Canonical config custody: process cache lock, then reentrant per-file OS lock."""
from contextlib import contextmanager
from functools import wraps
from pathlib import Path


@contextmanager
def config_transaction(path=None):
    from hermes_cli import config
    path = (Path(path) if path is not None else config.get_config_path()).resolve()
    # Every entry takes the existing process RLock first, including nested save_config calls.
    with config._CONFIG_LOCK, config._env_write_lock(path):
        yield


def transactional_config(*, path_arg=False):
    def decorate(function):
        @wraps(function)
        def transaction(*args, **kwargs):
            path = args[0] if path_arg and args else kwargs.get("config_path") if path_arg else None
            with config_transaction(path):
                return function(*args, **kwargs)
        return transaction
    return decorate


@transactional_config(path_arg=True)
def write_config_state(config_path, data, *, allow_omissions, extra_content_on_create=None):
    """Shared canonical writer, preserving the existing omission and unreadable-file guards."""
    from hermes_cli import config
    from utils import atomic_roundtrip_yaml_save

    config._refuse_failed_read(config_path, data)
    if not allow_omissions:
        existing = config.require_readable_config_before_write(config_path)
        omitted = config._omitted_config_paths(existing, data)
        if omitted:
            shown = ", ".join(omitted[:12])
            if len(omitted) > 12:
                shown += f", +{len(omitted) - 12} more"
            exc = ValueError(f"omitted config paths: {shown}")
            raise config._refuse_overwrite(
                config_path, "would lose settings omitted by this write", exc,
                "Pass the complete current config, or use atomic_config_replace() only when deletion by omission is deliberate.",
            ) from exc
    atomic_roundtrip_yaml_save(config_path, data, extra_content_on_create=extra_content_on_create)
