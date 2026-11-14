"""Encrypted website rules remain authoritative across served profile switches."""
import pytest

from hermes_security import io, vault
from tools import website_policy


@pytest.fixture
def homes(tmp_path, monkeypatch):
    pair = [tmp_path / name for name in ('a', 'b')]
    monkeypatch.setenv('HERMES_HOME', str(pair[0]))
    monkeypatch.setattr(website_policy, '_cached_policy', None)
    for home, enabled in zip(pair, ('false', 'true')):
        home.mkdir()
        vault.init_vault(home, 'website-policy-test')
        vault.unlock(home, 'website-policy-test')
        io.write_state_text(home / 'config.yaml',
                            f'security:\n  website_blocklist:\n    enabled: {enabled}\n    domains: [blocked.example]\n',
                            purpose='config')
    try:
        yield pair
    finally:
        vault.clear_vault_cache()


def test_profile_switch_respects_encrypted_blocklist_and_disabled_policy(homes):
    from agent import secret_scope
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from importlib import import_module
    import_module("tools.browser_tool")
    from tools.registry import registry
    import json

    secret_scope.set_multiplex_active(True)
    try:
        for home in (homes[0], homes[1], homes[0]):
            token = set_hermes_home_override(home)
            secret_token = secret_scope.set_secret_scope({}, profile_home=str(home))
            try:
                blocked = website_policy.check_website_access('https://blocked.example/page')
                assert bool(blocked) == (home == homes[1])
                if blocked:
                    result = json.loads(registry.get_entry('browser_navigate').handler({'url': 'https://blocked.example/page'}))
                    assert not result['success'] and 'website policy' in result['error'].lower()
            finally:
                secret_scope.reset_secret_scope(secret_token)
                reset_hermes_home_override(token)
    finally:
        secret_scope.set_multiplex_active(False)


def test_locked_website_policy_never_falls_back_to_allow(homes, monkeypatch):
    from hermes_security.errors import VaultLockedError

    monkeypatch.delenv('HERMES_VAULT_PRIVATE_KEY', raising=False)
    monkeypatch.delenv('HERMES_MASTER_PASSWORD', raising=False)
    vault.lock_now(homes[1])
    with pytest.raises(VaultLockedError):
        website_policy.check_website_access('https://blocked.example', config_path=homes[1] / 'config.yaml')


def test_extract_dispatch_preserves_locked_policy_refusal(homes):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from hermes_security.errors import VaultLockedError
    from tools.web_tools_extract import _extract_safe_urls

    vault.lock_now(homes[1])
    token = set_hermes_home_override(homes[1])
    try:
        with (
            patch('tools.web_tools_extract._dispatch_extract', new_callable=AsyncMock) as dispatch,
            patch('tools.web_result_cache.extract_cache_get') as cache_get,
        ):
            with pytest.raises(VaultLockedError):
                asyncio.run(_extract_safe_urls(SimpleNamespace(name='fixture'), ['https://blocked.example'], None))
            assert dispatch.await_count == 0 and cache_get.call_count == 0
    finally:
        reset_hermes_home_override(token)
