"""Opaque native credentials remain a profile-local model/log egress boundary."""
from contextlib import contextmanager

import pytest

from agent import redact
from agent.secret_scope import reset_secret_scope, set_secret_scope
from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from hermes_security import migrate, vault


@contextmanager
def _home_scope(home):
    token = set_hermes_home_override(home)
    try:
        yield
    finally:
        reset_hermes_home_override(token)


def _sealed_home(home, credential):
    home.mkdir()
    (home / '.env').write_text(f'ACME_SERVICE_TOKEN={credential}\nTOKEN_COUNT=metadata-count\n')
    report = migrate.migrate_home(home, 'synthetic-redaction-fixture-password')
    assert report.ok, report
    vault.unlock(home, 'synthetic-redaction-fixture-password')
    return home


@pytest.mark.parametrize('bound_scope', [False, True])
def test_real_vault_credentials_are_exact_scrubbed_only_for_the_owning_profile(tmp_path, monkeypatch, bound_scope):
    secret_a, secret_b = 'opaque-profile-alpha-credential', 'opaque-profile-bravo-credential'
    home_a = _sealed_home(tmp_path / 'a', secret_a)
    home_b = _sealed_home(tmp_path / 'b', secret_b)
    monkeypatch.setenv('OTHER_SERVICE_TOKEN', 'unscoped-process-credential')
    monkeypatch.setattr(redact, '_redact_enabled', lambda: False)  # vault credentials remain a hard boundary
    text = f'{secret_a} {secret_b} unscoped-process-credential metadata-count'
    for home, own, other in ((home_a, secret_a, secret_b), (home_b, secret_b, secret_a), (home_a, secret_a, secret_b)):
        with _home_scope(home):
            token = set_secret_scope({'ACME_SERVICE_TOKEN': own}, profile_home=str(home)) if bound_scope else None
            try:
                result = redact.redact_sensitive_text(text)
                assert own not in result
                assert other in result
                assert 'unscoped-process-credential' in result
                assert 'metadata-count' in result
            finally:
                if token is not None:
                    reset_secret_scope(token)
    vault.lock_now(home_a)
    vault.lock_now(home_b)


def test_credential_redaction_has_no_eviction_or_post_lock_plaintext_cache(tmp_path):
    secret = 'opaque-active-profile-credential'
    home = _sealed_home(tmp_path / 'home', secret)
    with _home_scope(home):
        for number in range(100):
            redact.register_vault_redaction_value(f'browser-only-password-{number}')
        assert secret not in redact.redact_registered_vault_values(secret)
        vault.lock_now(home)
        assert redact.redact_registered_vault_values(secret) == secret
        redact.clear_vault_redaction_values()


def test_foreign_bound_scope_is_not_borrowed_by_home_bound_background_redaction(tmp_path):
    home_a = _sealed_home(tmp_path / 'a', 'opaque-home-alpha-token')
    home_b = _sealed_home(tmp_path / 'b', 'opaque-home-bravo-token')
    token = set_secret_scope({'ACME_SERVICE_TOKEN': 'opaque-home-alpha-token'}, profile_home=str(home_a))
    try:
        with _home_scope(home_b):
            result = redact.redact_registered_vault_values('opaque-home-alpha-token opaque-home-bravo-token')
            assert 'opaque-home-alpha-token' in result
            assert 'opaque-home-bravo-token' not in result
    finally:
        reset_secret_scope(token)
        vault.lock_now(home_a)
        vault.lock_now(home_b)


def test_rotated_profile_credentials_and_live_scope_keep_separate_lifetimes(tmp_path):
    old, new = "opaque-live-session-token", "opaque-rotated-profile-token"
    home = _sealed_home(tmp_path / "home", old)
    token = set_secret_scope({"ACME_SERVICE_TOKEN": old}, profile_home=str(home))
    try:
        with _home_scope(home):
            vault.get_vault(home).write_bytes(home / ".env", f"ACME_SERVICE_TOKEN={new}\n".encode(), purpose="env")
            result = redact.redact_registered_vault_values(f"{old} {new}")
            assert old not in result and new not in result
    finally:
        reset_secret_scope(token)
    with _home_scope(home):
        result = redact.redact_registered_vault_values(f"{old} {new}")
        assert old in result and new not in result
    vault.lock_now(home)


def test_ancestor_vault_authenticates_only_each_profiles_own_env(tmp_path):
    from hermes_security.errors import VaultIntegrityError

    owner = _sealed_home(tmp_path / "owner", "opaque-ancestor-owner-token")
    profile = owner / "profiles" / "child"
    profile.mkdir(parents=True)
    env = profile / ".env"
    vault.get_vault(owner).write_bytes(env, b"ACME_SERVICE_TOKEN=opaque-child-profile-token\n", purpose="env")
    with _home_scope(profile):
        result = redact.redact_registered_vault_values("opaque-ancestor-owner-token opaque-child-profile-token")
        assert "opaque-child-profile-token" not in result
        assert "opaque-ancestor-owner-token" in result
        raw = bytearray(env.read_bytes())
        raw[-1] ^= 1
        env.write_bytes(raw)
        with pytest.raises(VaultIntegrityError):
            redact.redact_registered_vault_values("opaque-child-profile-token")
    vault.lock_now(owner)
