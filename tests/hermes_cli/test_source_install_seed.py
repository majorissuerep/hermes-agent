"""Missing installer state is seeded encrypted; existing state is never replaced."""
import os
from pathlib import Path
import sys

import pytest

from hermes_security import io, vault

pytestmark = pytest.mark.platforms("posix")
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("existing", [False, True])
def test_config_stage_seeds_only_missing_files_through_unlocked_owner(tmp_path, monkeypatch, existing):
    from hermes_cli.source_install_session import run_install_session

    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    vault.init_vault(home, "synthetic-seed-password")
    vault.unlock(home, "synthetic-seed-password")
    originals = {}
    if existing:
        # Source repair must not bless or replace untrusted clobbered state.
        for name in ("config.yaml", ".env"):
            path = home / name
            path.write_bytes(b"untrusted existing state\n")
            originals[name] = path.read_bytes()
    script = '''seed_python="$3"
source "$1/scripts/install.sh" --manifest --hermes-home "$2" --dir "$1"
bootstrap_python() { boot_py="$seed_python"; }
stage_config
'''
    # Inject only the already provisioned test interpreter, not application I/O.
    command = ["bash", "-c", script, "installer-seed-test", str(ROOT), str(home), sys.executable]
    try:
        assert run_install_session(command) == 0
        for name, purpose in (("config.yaml", "config"), (".env", "env")):
            raw = (home / name).read_bytes()
            if existing:
                assert raw == originals[name]
            else:
                assert raw.startswith(b"HRMVAULT\x00")
                assert io.read_text(home / name, purpose=purpose)
                assert os.stat(home / name).st_mode & 0o077 == 0
    finally:
        vault.lock_now(home)


def test_config_edit_seeder_never_writes_plaintext_inside_vault(tmp_path, monkeypatch):
    from hermes_cli.config import read_raw_config, seed_config_file

    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    vault.init_vault(home, "synthetic-seed-password")
    vault.unlock(home, "synthetic-seed-password")
    try:
        assert seed_config_file(home / "config.yaml")
        assert (home / "config.yaml").read_bytes().startswith(b"HRMVAULT\x00")
        assert read_raw_config()["model"]["provider"] == ""
    finally:
        vault.lock_now(home)
