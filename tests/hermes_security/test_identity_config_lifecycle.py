"""Migrated CLI settings and identity must survive a fresh-process unlock."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from hermes_security import migrate, vault

PASSWORD = "synthetic-consumer-test"
ROOT = Path(__file__).resolve().parents[2]


def _restart(home, code):
    env = dict(os.environ, HERMES_HOME=str(home))
    env.pop("HERMES_VAULT_PRIVATE_KEY", None)
    env.pop("HERMES_IGNORE_USER_CONFIG", None)
    prelude = (
        "from pathlib import Path; import os; "
        "from hermes_security import vault; "
        "home = Path(os.environ['HERMES_HOME']); "
        f"vault.unlock(home, {PASSWORD!r}); "
    )
    result = subprocess.run([sys.executable, "-c", prelude + code], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


def test_migrated_cli_settings_match_effective_loader_after_restart(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(
        "model:\n  default: lifecycle-marker\nagent:\n  max_turns: 17\n", encoding="utf-8")
    try:
        assert migrate.migrate_home(home, PASSWORD).ok
        vault.clear_vault_cache()
        _restart(home, """
import cli
from hermes_cli.config_effective import load_user_config_effective
classic = cli.load_cli_config()
effective = load_user_config_effective()
assert classic['model']['default'] == effective['model']['default'] == 'lifecycle-marker'
assert classic['agent']['max_turns'] == effective['agent']['max_turns'] == 17
""")
    finally:
        vault.clear_vault_cache()


@pytest.mark.parametrize("fault", ["locked", "tampered", "plaintext"])
def test_cli_vault_errors_do_not_become_defaults(tmp_path, fault):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("agent:\n  max_turns: 17\n", encoding="utf-8")
    try:
        assert migrate.migrate_home(home, PASSWORD).ok
        vault.clear_vault_cache()
        _restart(home, f"""
import cli
import pytest
from hermes_security.errors import VaultError
path = home / 'config.yaml'
if {fault!r} == 'locked':
    vault.clear_vault_cache()
elif {fault!r} == 'tampered':
    raw = path.read_bytes()
    path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
else:
    path.write_text('agent: {{max_turns: 99}}')
with pytest.raises(VaultError):
    cli.load_cli_config()
""")
    finally:
        vault.clear_vault_cache()


@pytest.mark.parametrize("seed", ["migrated", "first-run", "cyclic", "dangling"])
def test_soul_identity_is_usable_and_sealed_after_restart(tmp_path, seed):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("{}\n", encoding="utf-8")
    if seed == "migrated":
        (home / "SOUL.md").write_text("Concise identity lifecycle marker.", encoding="utf-8")
    try:
        assert migrate.migrate_home(home, PASSWORD).ok
        vault.clear_vault_cache()
        _restart(home, f"""
from agent.prompt_builder import load_soul_md
from hermes_cli.config import DEFAULT_SOUL_MD, _ensure_default_soul_md
from hermes_security import io
if {seed!r} in ('cyclic', 'dangling'):
    target = 'SOUL.md' if {seed!r} == 'cyclic' else 'missing/SOUL.md'
    (home / 'SOUL.md').unlink(missing_ok=True)
    (home / 'SOUL.md').symlink_to(home / target)
_ensure_default_soul_md(home)
expected = 'Concise identity lifecycle marker.' if {seed!r} == 'migrated' else DEFAULT_SOUL_MD.strip()
assert load_soul_md(home_override=home) == expected
assert io.read_text(home / 'SOUL.md', purpose='state').strip() == expected
assert (home / 'SOUL.md').read_bytes().startswith(b'HRMVAULT\\x00')
""")
    finally:
        vault.clear_vault_cache()
