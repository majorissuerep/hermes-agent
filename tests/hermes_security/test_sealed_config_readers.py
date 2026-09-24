"""Fork regression: sealed config.yaml must reach every config reader, not just the CLI's.

Incident (luoman-MS73-HB1, 2026-09-25): the vaulted gateway booted on env-only config because
``gateway.config_loader.read_yaml_layers`` and ``utils.load_yaml_file_readonly`` (terminal scope's
reader) opened the sealed envelope with a plain ``open()`` and failed on UTF-8 decode — platform
authz (telegram.allowed_chats) silently ran on defaults and the terminal tool was hard-down with
``TerminalPolicyUnavailable``.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pytest

from hermes_security import migrate as mig
from hermes_security import vault as hv

PW = "sealed-config-reader-pw"


@pytest.fixture()
def sealed_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        "terminal:\n  backend: local\n  timeout: 123\ntelegram:\n  allowed_chats: [42]\n", encoding="utf-8"
    )
    (home / ".env").write_text("TERMINAL_ENV=local\nOPENAI_API_KEY=x\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert mig.migrate_home(home, PW).ok
    hv.clear_vault_cache()
    hv.unlock(home, PW)
    yield home
    hv.clear_vault_cache()


def _is_sealed(path: Path) -> bool:
    return path.read_bytes().startswith(b"HRMVAULT\x00")


def test_gateway_yaml_layer_reads_sealed_config(sealed_home):
    from gateway import config_loader

    assert _is_sealed(sealed_home / "config.yaml"), "fixture must seal config.yaml"
    data = config_loader.read_yaml_layers(sealed_home)
    assert data.get("telegram", {}).get("allowed_chats") == [42]


def test_gateway_yaml_layer_locked_vault_degrades_to_empty(sealed_home, monkeypatch):
    """Locked vault → empty layer (caller warns + falls back), never a decode crash."""
    from gateway import config_loader

    hv.clear_vault_cache()  # locked now
    monkeypatch.delenv("HERMES_MASTER_PASSWORD", raising=False)
    data = config_loader.read_yaml_layers(sealed_home)
    assert data == {}


def test_load_yaml_file_readonly_reads_sealed_config(sealed_home):
    from utils import load_yaml_file_readonly

    assert _is_sealed(sealed_home / "config.yaml")
    raw = load_yaml_file_readonly(sealed_home / "config.yaml")
    assert raw.get("terminal", {}).get("timeout") == 123


def test_terminal_scope_resolves_over_sealed_config(sealed_home):
    """The incident's user-visible symptom: the terminal tool was down (policy refusal)."""
    from tools import terminal_scope

    scope = terminal_scope.build_profile_terminal_scope(sealed_home)
    assert isinstance(scope, dict)
    # TERMINAL_* keys from the sealed .env and config.yaml reached the policy.
    assert scope.get("TERMINAL_ENV") == "local"
