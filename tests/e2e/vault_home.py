"""Synthetic encrypted homes and sealed descriptor handoff for real E2E children."""

from __future__ import annotations

import os
import json
import sys
from contextlib import contextmanager
from pathlib import Path
import secrets
import subprocess
from threading import RLock
from unittest.mock import patch

_FIXTURE_PASSWORDS: dict[Path, str] = {}
_PM_HOME_LOCK = RLock()


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


@contextmanager
def _fixture_pm_home(home: Path):
    """Match the child's home for both scoped readers and process-root PM stores."""
    import hermes_constants as constants

    # Provider scenarios prepare homes in threads, but PM's process-root/store
    # readers are deliberately global. Never interleave those temporary roots.
    with _PM_HOME_LOCK:
        home = home.resolve()
        token = constants.set_hermes_home_override(home)
        try:
            with patch.dict(os.environ, {"HERMES_HOME": str(home)}), patch.object(
                constants, "_PINNED_PROCESS_HERMES_HOME", str(home)
            ):
                yield
        finally:
            constants.reset_hermes_home_override(token)


def _share_fixture_download_cache(home: Path) -> None:
    """Opt in to CI's existing content-addressed downloads, never its runtime/state."""
    from hermes_constants import get_default_hermes_root

    configured = os.environ.get("UV_CACHE_DIR")
    if not configured:
        return
    shared = Path(configured)
    if not shared.is_absolute() or not shared.is_dir():
        raise ValueError("UV_CACHE_DIR must name an existing absolute download-cache directory")
    shared = shared.resolve(strict=True)
    cache = get_default_hermes_root(home=home) / "cache" / "uv"
    if cache.is_symlink() and cache.resolve(strict=True) == shared:
        return
    if cache.exists() or cache.is_symlink():
        raise FileExistsError(f"Refusing to replace fixture download cache: {cache}")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.symlink_to(shared, target_is_directory=True)


def _prepare_fixture_pm(home: Path, source: Path, extras: tuple[str, ...], *, tui: bool = False) -> None:
    """PM owns a separate process: pytest may reset its own home between test cases."""
    with _PM_HOME_LOCK:
        _share_fixture_download_cache(home)
        seal_fixture_home(home)
        env = {**os.environ, "HERMES_HOME": str(home.resolve())}
        argv = [sys.executable, "-m", "tests.e2e.vault_home", str(home.resolve()),
                str(source.resolve()), json.dumps(extras), "tui" if tui else "runtime"]
        with spawn_unlocked(argv, env=env, cwd=source, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True) as process:
            try:
                output, _ = process.communicate(timeout=600)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise
            if process.returncode:
                raise RuntimeError(f"fixture PM preparation failed ({process.returncode}): {output[-12000:]}")


def prepare_fixture_runtime(home: Path, source: Path, *, extras: tuple[str, ...] = ("all",)) -> None:
    """Prepare a real owning PM selection before a sealed child imports the app."""
    _prepare_fixture_pm(home, source, extras)


def prepare_fixture_tui(home: Path, source: Path) -> None:
    """Prepare PM tools and verify the canonical compiler's existing build receipt."""
    _prepare_fixture_pm(home, source, ("all",), tui=True)


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


if __name__ == "__main__":
    from hermes_security.handoff import adopt_inherited_key
    from pm import sync_venv

    assert adopt_inherited_key(), "fixture PM child must inherit its synthetic vault"
    selected_home, selected_source = Path(sys.argv[1]), Path(sys.argv[2])
    with _fixture_pm_home(selected_home):
        sync_venv(extras=json.loads(sys.argv[3]), explicit=True, project_root=selected_source)
        if sys.argv[4] == "tui":
            from hermes_cli.source_build import source_build_env, source_product_current

            source_build_env(explicit=True)
            assert source_product_current(selected_source, "tui", selected_source / "ui-tui/dist"), (
                "public TUI acceptance requires the canonical compiler's current build receipt")
