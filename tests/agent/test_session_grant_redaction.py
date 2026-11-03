"""Local capabilities remain redacted after registry churn and across processes."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from agent import redact
from hermes_security import vault
from hermes_security.session_authority import start_session_authority
from tests.agent.test_redact_profile_credentials import _home_scope, _sealed_home


@pytest.mark.parametrize("closed", [False, True])
def test_session_grant_cannot_escape_after_ordinary_secret_registry_eviction(tmp_path, monkeypatch, closed):
    home = _sealed_home(tmp_path / "profile", "public-fixture-credential")
    with _home_scope(home):
        authority = start_session_authority(home, "redaction-contract")
        try:
            for index in range(100):
                redact.register_vault_redaction_value(f"public-registry-fixture-{index}")
            if closed:
                authority.close()
                redact.clear_vault_redaction_values()
            monkeypatch.setattr(redact, "_redact_enabled", lambda: False)
            result = redact.redact_sensitive_text("prefix" + authority.token + "suffix")
            leaked = authority.token in result
            assert not leaked
        finally:
            authority.close()
            vault.lock_now(home)


def test_fresh_child_redacts_a_standalone_grant_without_parent_secret_registry(tmp_path):
    home = _sealed_home(tmp_path / "profile", "public-fixture-credential")
    with _home_scope(home):
        authority = start_session_authority(home, "child-redaction-contract")
        try:
            env = {key: value for key, value in os.environ.items() if not key.startswith("HERMES_")}
            env.update(HOME=str(home), HERMES_HOME=str(home), CANARY=authority.token)
            script = ("import os; from agent import redact; redact._redact_enabled=lambda:False; "
                      "print(redact.redact_sensitive_text(os.environ['CANARY']))")
            result = subprocess.run([sys.executable, "-c", script], env=env, check=True,
                                    text=True, capture_output=True, timeout=15)
            leaked = authority.token in result.stdout or authority.token in result.stderr
            assert not leaked
        finally:
            authority.close()
            vault.lock_now(home)
