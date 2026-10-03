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
