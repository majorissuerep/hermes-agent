"""Master-password gate policy: EVERY state-touching command is gated.

The gate is the fork's key feature. These tests pin the contract:
- vault-less home + no escape -> SystemExit(2), both interactive and not
- vault present + locked -> prompts / HERMES_MASTER_PASSWORD / wrong pw exit
- read-only surfaces (secure-vault, --version, update --check) pass
- HERMES_ALLOW_NO_VAULT=1 is the only bypass
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from hermes_cli import vault_gate
from hermes_security import vault as hv


@pytest.fixture(autouse=True)
def _arm_gate(monkeypatch, tmp_path):
    """No CI escape inside these tests; isolated home."""

    monkeypatch.delenv("HERMES_ALLOW_NO_VAULT", raising=False)
    monkeypatch.delenv("HERMES_MASTER_PASSWORD", raising=False)
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    hv.clear_vault_cache()
    yield home
    hv.clear_vault_cache()


def _args(command=None, **kw):
    return SimpleNamespace(command=command, version=False, **kw)


def test_no_vault_refuses_noninteractive(_arm_gate, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit) as e:
        vault_gate.gate_startup(_args("config"))
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "master password" in err.lower()


def test_no_vault_offers_migration_on_tty(_arm_gate, monkeypatch):
    home = _arm_gate
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    import getpass

    answers = iter(["y", "pw-tty-offer-99", "pw-tty-offer-99"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    monkeypatch.setattr(getpass, "getpass", lambda *a, **k: next(answers))
    # after migrate the vault exists; gate should return cleanly
    vault_gate.gate_startup(_args("config"))
    assert hv.vault_exists(home)


def test_chat_is_gated(_arm_gate, monkeypatch):
    """command=None (interactive chat) must NOT bypass."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit):
        vault_gate.gate_startup(_args(None))


def test_gateway_command_gated(_arm_gate, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit):
        vault_gate.gate_startup(_args("gateway"))


def test_read_only_surfaces_pass(_arm_gate):
    vault_gate.gate_startup(_args("secure-vault"))
    vault_gate.gate_startup(_args("vault"))
    vault_gate.gate_startup(SimpleNamespace(command=None, version=True))
    vault_gate.gate_startup(_args("update", check=True))


def test_update_without_check_is_gated(_arm_gate, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit):
        vault_gate.gate_startup(_args("update"))


def test_allow_no_vault_escape(_arm_gate, monkeypatch):
    monkeypatch.setenv("HERMES_ALLOW_NO_VAULT", "1")
    vault_gate.gate_startup(_args("config"))  # passes


def test_locked_vault_env_password_unlocks(_arm_gate, monkeypatch):
    home = _arm_gate
    hv.init_vault(home, "gate-pw-1", _allow_existing_state=True)
    monkeypatch.setenv("HERMES_MASTER_PASSWORD", "gate-pw-1")
    vault_gate.gate_startup(_args("config"))
    assert hv.is_unlocked(home)


def test_locked_vault_wrong_password_exits(_arm_gate, monkeypatch, capsys):
    home = _arm_gate
    hv.init_vault(home, "gate-pw-2", _allow_existing_state=True)
    monkeypatch.setenv("HERMES_MASTER_PASSWORD", "wrong-one")
    with pytest.raises(SystemExit) as e:
        vault_gate.gate_startup(_args("config"))
    assert e.value.code == 2


def test_locked_vault_noninteractive_without_password_exits(_arm_gate, monkeypatch):
    home = _arm_gate
    hv.init_vault(home, "gate-pw-3", _allow_existing_state=True)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit) as e:
        vault_gate.gate_startup(_args("config"))
    assert e.value.code == 2
