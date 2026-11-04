"""Pure Bash decisions refuse new headless migration before takeover effects."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "takeover.sh"
pytestmark = pytest.mark.platforms("linux", "macos")


def _environment(tmp_path):
    user = tmp_path / "user"
    user.mkdir()
    return {"PATH": os.environ["PATH"], "HOME": str(user),
            "HERMES_HOME": str(tmp_path / "state"), "TMPDIR": str(tmp_path / "scratch"),
            "HERMES_MASTER_PASSWORD": "synthetic ignored environment password"}


def test_missing_headless_output_refuses_before_source_pm_or_data_effects(tmp_path):
    env = _environment(tmp_path)
    touched = tmp_path / "preparation-started"
    env["PROBE_EFFECT"] = str(touched)
    probe = r'''
source "$1"
_tty_available() { return 1; }
require_quiescent_home() { printf guard > "$PROBE_EFFECT"; }
swap_source() { printf source >> "$PROBE_EFFECT"; return 73; }
main
'''
    result = subprocess.run(["bash", "-c", probe, "takeover-probe", str(SCRIPT)],
                            env=env, capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode != 0
    assert "HERMES_VAULT_KEY_OUT" in result.stderr
    assert not touched.exists()
    assert not Path(env["HERMES_HOME"]).exists()
    assert not Path(env["TMPDIR"]).exists()


@pytest.mark.parametrize("same_group", [False, True], ids=["inherited-root", "same-group-setgid"])
def test_preparation_uses_unique_private_scratch_without_changing_caller_directory(tmp_path, same_group):
    env = _environment(tmp_path)
    env["HERMES_VAULT_KEY_OUT"] = str(tmp_path / "external-new.key")
    scratch_root = Path(env["TMPDIR"])
    scratch_root.mkdir(mode=0o755)
    if same_group:
        os.chown(scratch_root, -1, os.getegid())
        scratch_root.chmod(0o2755)
    original_mode = scratch_root.stat().st_mode & 0o7777
    occupied = scratch_root / "hermes-takeover"
    occupied.mkdir()
    (occupied / "retained").write_text("caller-owned bytes")
    log = tmp_path / "scratch-selection"
    env["PROBE_SCRATCH"] = str(log)
    probe = r'''
source "$1"
_tty_available() { return 1; }
require_quiescent_home() { return 0; }
swap_source() {
    printf '%s\n' "$SCRATCH_DIR" > "$PROBE_SCRATCH"
    test "$(stat -c '%a' "$SCRATCH_DIR" 2>/dev/null || stat -f '%OLp' "$SCRATCH_DIR")" = 700
    return 73
}
main
'''
    result = subprocess.run(["bash", "-c", probe, "takeover-probe", str(SCRIPT)],
                            env=env, capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 73
    chosen = Path(log.read_text().strip())
    assert chosen.parent == scratch_root and chosen != occupied
    assert not chosen.exists(), "owned scratch is removed even when source preparation fails"
    assert scratch_root.stat().st_mode & 0o7777 == original_mode
    assert (occupied / "retained").read_text() == "caller-owned bytes"


@pytest.mark.parametrize("mode", ["explicit-path", "terminal", "existing-vault"])
def test_migration_preserves_explicit_path_terminal_and_existing_vault(tmp_path, mode):
    env = _environment(tmp_path)
    log = tmp_path / "arguments"
    env["PROBE_ARGUMENTS"] = str(log)
    launcher = Path(env["HOME"]) / ".local" / "bin" / "hermes"
    launcher.parent.mkdir(parents=True)
    launcher.write_text('#!/usr/bin/env bash\nprintf "%s\\0" "$@" > "$PROBE_ARGUMENTS"\n')
    launcher.chmod(0o700)
    if mode == "explicit-path":
        env["HERMES_VAULT_KEY_OUT"] = str(tmp_path / "external-new.key")
    if mode == "existing-vault":
        home = Path(env["HERMES_HOME"])
        home.mkdir()
        (home / ".hermes-vault").write_bytes(b"synthetic retained vault metadata")
    tty_status = "0" if mode == "terminal" else "1"
    result = subprocess.run(["bash", "-c", 'source "$1"; probe_tty_status=$2; _tty_available() { return "$probe_tty_status"; }; migrate_state',
                             "takeover-probe", str(SCRIPT), tty_status],
                            env=env, capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0
    if mode == "existing-vault":
        assert not log.exists()
        assert (Path(env["HERMES_HOME"]) / ".hermes-vault").read_bytes() == b"synthetic retained vault metadata"
    else:
        arguments = log.read_bytes().rstrip(b"\0").decode().split("\0")
        expected = ["secure-vault", "migrate", "--yes"]
        if mode == "explicit-path":
            expected += ["--key-only", "--key-out", env["HERMES_VAULT_KEY_OUT"]]
        assert arguments == expected
