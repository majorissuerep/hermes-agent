"""Synthetic encrypted homes and sealed descriptor handoff for real E2E children."""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import subprocess

_FIXTURE_PASSWORDS: dict[Path, str] = {}


def write_encrypted_home(home: Path, base_url: str, **kwargs) -> Path:
    from tests.fakes.fake_llm_provider import write_hermes_home

    write_hermes_home(home, base_url, **kwargs)
    seal_fixture_home(home)
    return home


def seal_fixture_home(home: Path) -> None:
    """Seal a fixture writer's config/env; retain unlock authority only in memory."""
    from hermes_security import migrate, vault

    if not vault.vault_exists(home):
        password = secrets.token_urlsafe(24)
        report = migrate.migrate_home(home, password)
        assert report.ok, report
        # Empty synthetic homes take migration's metadata-only branch.
        vault.unlock(home, password)
        _FIXTURE_PASSWORDS[home.resolve()] = password
        return
    owner = vault.get_vault(home)
    for name, purpose in (("config.yaml", "config"), (".env", "env")):
        path = home / name
        if path.is_file():
            data = path.read_bytes()
            if not data.startswith(b"HRMVAULT\x00"):
                owner.write_bytes(path, data, purpose=purpose)


def fixture_password(home: Path) -> str:
    """Only fixture-owned synthetic passwords may be typed at a real terminal gate."""
    return _FIXTURE_PASSWORDS[home.resolve()]


def spawn_unlocked(argv, *, env: dict[str, str], **kwargs) -> subprocess.Popen:
    """A real child consumes one inherited key pipe at its public/private vault gate."""
    from hermes_security.handoff import prepare_child_key

    handoff = prepare_child_key()
    assert handoff is not None, "E2E parent must own a synthetic unlocked vault"
    fd, additions = handoff
    try:
        return subprocess.Popen(argv, env={**env, **additions}, pass_fds=(fd,), **kwargs)
    finally:
        os.close(fd)


def connect_state(path: Path):
    """Read the actual SQLCipher transcript without plaintext copies or fallback."""
    from hermes_security import sqlite

    conn = sqlite.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite.row_class()
    return conn

def run_unlocked(argv, *, env, timeout, capture_output=False, check=False, **kwargs):
    """Run an owning Hermes child with the fixture's one-use key descriptor."""
    if capture_output:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with spawn_unlocked(argv, env=env, **kwargs) as proc:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise
        result = subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result
