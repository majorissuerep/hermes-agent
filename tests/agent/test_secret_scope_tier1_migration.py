"""Regression tests for the Tier-1 core-gateway secret-scope migration.

Class-closure follow-up to the profile secret-scope cluster (#76462 /
#76574): representative call sites from each migrated cluster are exercised
against the three canonical scope semantics:

- scoped value wins (the installed profile's secret is used),
- scoped miss does NOT borrow the process env under multiplex (no-borrow),
- unscoped-under-multiplex behavior per pattern:
  * in-turn sites (get_secret direct) propagate/honor UnscopedSecretError
    semantics via get_secret's verdict,
  * startup sites (Slack pattern) fall back to os.environ on
    UnscopedSecretError.
"""

import pytest

from agent import secret_scope as ss

@pytest.fixture(autouse=True)
def _reset_multiplex():
    ss.set_multiplex_active(False)
    yield
    ss.set_multiplex_active(False)

class _Scope:
    """Context manager installing a secret scope."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.token = None

    def __enter__(self):
        self.token = ss.set_secret_scope(self.mapping)
        return self

    def __exit__(self, *exc):
        ss.reset_secret_scope(self.token)

class _ExplodingScope(dict):
    """A bound secret scope whose resolution fails (resolver/backend error)."""
    def get(self, name, default=None):
        raise RuntimeError("resolver boom")


# ── Cluster C: managed tool gateway token override ─────────────────────────

class TestToolGatewayUserToken:
    def test_scoped_value_wins(self, monkeypatch):
        from tools.managed_tool_gateway import _read_user_token_override

        monkeypatch.setenv("TOOL_GATEWAY_USER_TOKEN", "env-tok")
        ss.set_multiplex_active(True)
        with _Scope({"TOOL_GATEWAY_USER_TOKEN": "scoped-tok"}):
            assert _read_user_token_override() == "scoped-tok"

    def test_scoped_miss_no_borrow(self, monkeypatch):
        from tools.managed_tool_gateway import _read_user_token_override

        monkeypatch.setenv("TOOL_GATEWAY_USER_TOKEN", "other-profile-tok")
        ss.set_multiplex_active(True)
        with _Scope({"UNRELATED": "x"}):
            assert _read_user_token_override() is None

    def test_unscoped_multiplex_falls_back(self, monkeypatch):
        from tools.managed_tool_gateway import _read_user_token_override

        monkeypatch.setenv("TOOL_GATEWAY_USER_TOKEN", "own-env-tok")
        ss.set_multiplex_active(True)
        assert _read_user_token_override() == "own-env-tok"

    def test_scope_failure_never_borrows_env(self, monkeypatch):
        from tools.managed_tool_gateway import _read_user_token_override

        monkeypatch.setenv("TOOL_GATEWAY_USER_TOKEN", "other-profile-tok")
        ss.set_multiplex_active(True)
        with _Scope(_ExplodingScope()):
            with pytest.raises(RuntimeError, match="resolver boom"):
                _read_user_token_override()

class TestOpenRouterCheckApiKey:
    def test_scoped_value_wins(self, monkeypatch):
        from tools.openrouter_client import check_api_key

        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        ss.set_multiplex_active(True)
        with _Scope({"OPENROUTER_API_KEY": "sk-or-scoped"}):
            assert check_api_key() is True

    def test_scoped_miss_no_borrow(self, monkeypatch):
        from tools.openrouter_client import check_api_key

        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-other-profile")
        ss.set_multiplex_active(True)
        with _Scope({"UNRELATED": "x"}):
            assert check_api_key() is False

    def test_scope_failure_never_borrows_env(self, monkeypatch):
        from tools.openrouter_client import check_api_key

        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-other-profile")
        ss.set_multiplex_active(True)
        with _Scope(_ExplodingScope()):
            with pytest.raises(RuntimeError, match="resolver boom"):
                check_api_key()

# ── Cluster D: auxiliary client key resolution ──────────────────────────────

class TestAuxiliaryScopedKeyEnv:
    def test_scoped_value_wins(self, monkeypatch):
        from agent.auxiliary_client import _scoped_key_env

        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
        ss.set_multiplex_active(True)
        with _Scope({"OPENROUTER_API_KEY": "sk-scoped"}):
            assert _scoped_key_env("OPENROUTER_API_KEY") == "sk-scoped"

    def test_scoped_miss_no_borrow(self, monkeypatch):
        from agent.auxiliary_client import _scoped_key_env

        monkeypatch.setenv("OPENAI_API_KEY", "sk-other-profile")
        ss.set_multiplex_active(True)
        with _Scope({"UNRELATED": "x"}):
            assert _scoped_key_env("OPENAI_API_KEY") == ""

    def test_unscoped_multiplex_falls_back(self, monkeypatch):
        from agent.auxiliary_client import _scoped_key_env

        monkeypatch.setenv("OPENAI_API_KEY", "sk-own-env")
        ss.set_multiplex_active(True)
        assert _scoped_key_env("OPENAI_API_KEY") == "sk-own-env"

    def test_empty_name_returns_empty(self):
        from agent.auxiliary_client import _scoped_key_env

        assert _scoped_key_env("") == ""

    def test_scope_failure_never_borrows_env(self, monkeypatch):
        from agent.auxiliary_client import _scoped_key_env

        monkeypatch.setenv("OPENAI_API_KEY", "sk-other-profile")
        ss.set_multiplex_active(True)
        with _Scope(_ExplodingScope()):
            with pytest.raises(RuntimeError, match="resolver boom"):
                _scoped_key_env("OPENAI_API_KEY")


# ── Cluster F: hermes_cli config env readers ───────────────────────────────

class TestScopedEnvironGet:
    """``_scoped_environ_get`` (feeds ``get_env_value`` / ``get_env_value_prefer_dotenv``)
    documents that ``UnscopedSecretError`` propagates; a blanket ``except Exception``
    after the call would silently borrow the launch profile's env on a bound-scope
    failure."""

    def test_scope_failure_never_borrows_env(self, monkeypatch):
        from hermes_cli.config import _scoped_environ_get

        monkeypatch.setenv("SOME_PROFILE_KEY", "other-profile")
        ss.set_multiplex_active(True)
        with _Scope(_ExplodingScope()):
            with pytest.raises(RuntimeError, match="resolver boom"):
                _scoped_environ_get("SOME_PROFILE_KEY")

    def test_unscoped_multiplex_propagates(self, monkeypatch):
        from hermes_cli.config import _scoped_environ_get

        monkeypatch.setenv("SOME_PROFILE_KEY", "launch-env")
        ss.set_multiplex_active(True)
        with pytest.raises(ss.UnscopedSecretError):
            _scoped_environ_get("SOME_PROFILE_KEY")

    def test_single_profile_env_read(self, monkeypatch):
        from hermes_cli.config import _scoped_environ_get

        monkeypatch.setenv("SOME_PROFILE_KEY", "own-env")
        assert _scoped_environ_get("SOME_PROFILE_KEY") == "own-env"

# ── Cluster E: azure identity presence reads ────────────────────────────────
