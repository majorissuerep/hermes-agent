"""A local setup command always acts on its launch profile."""
from contextlib import contextmanager

@contextmanager
def config_profile_scope(profile=None):
    from hermes_cli.profiles import get_active_profile_name
    if profile and profile not in {"current", get_active_profile_name()}:
        raise ValueError("Select the profile with hermes --profile before setting up its local model")
    yield
