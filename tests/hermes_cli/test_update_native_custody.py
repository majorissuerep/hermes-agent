"""Completion stays native and takeover preserves both checkout and vault authority."""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from hermes_cli import _old_updater, update_lock


def test_native_bytecode_completion_does_not_start_a_historical_takeover(tmp_path, monkeypatch):
    from hermes_cli import main, update_cmd_maint

    cache = tmp_path / "agent" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "old.pyc").write_bytes(b"obsolete bytecode")
    monkeypatch.setattr(main, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(main, "_read_git_revision_fingerprint", lambda root: "new-checkout")

    def unexpected_takeover(request):
        return 91, {}

    monkeypatch.setattr(_old_updater, "_run_child", unexpected_takeover)
    monkeypatch.setattr(_old_updater, "_result", None)

    update_cmd_maint._sweep_bytecode_after_update("main")

    assert not cache.exists()
    assert (tmp_path / ".bytecode-fingerprint").read_text() == "new-checkout"


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("encrypted", [False, True])
def test_historical_takeover_child_inherits_checkout_and_encrypted_home(
    tmp_path, monkeypatch, encrypted,
):
    from hermes_cli.update_vault import child_vault
    from hermes_security import io, vault

    root, home = tmp_path / "checkout", tmp_path / "state"
    root.mkdir()
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    for name in ("HERMES_MASTER_PASSWORD", "HERMES_VAULT_PRIVATE_KEY", "HERMES_VAULT_KEY_FD"):
        monkeypatch.delenv(name, raising=False)
    if encrypted:
        vault.init_vault(home, "synthetic-parent-only-unlock")
        vault.unlock(home, "synthetic-parent-only-unlock")
        io.write_text(home / "config.yaml", "model: custody-canary\n", purpose="config")

    child = tmp_path / "join.py"
    child.write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from hermes_cli.update_lock import UpdateLock\n"
        "from hermes_cli.update_vault import adopt_keys\n"
        "lock=UpdateLock(path=Path(sys.argv[2])/'child-marker',install_root=Path(sys.argv[2]))\n"
        "assert lock.acquire(), lock.holder\n"
        "adopt_keys()\n"
        "if sys.argv[4] == 'True':\n"
        " from hermes_security.io import read_text\n"
        " assert read_text(Path(sys.argv[3])/'config.yaml',purpose='config') == 'model: custody-canary\\n'\n"
        "lock.release()\n",
        encoding="utf-8",
    )
    owner = update_lock.UpdateLock(path=tmp_path / "owner-marker", install_root=root)
    assert owner.acquire(), owner.holder
    try:
        env = dict(os.environ)
        with child_vault(env) as kwargs:
            code = _old_updater._run_in_custody(
                [sys.executable, "-I", str(child), str(Path(update_lock.__file__).parents[1]),
                 str(root), str(home), str(encrypted)],
                root, env=env, capture_output=True, timeout=30, **kwargs,
            )
        assert code == 0
        assert update_lock.checkout_lock_held(root)
    finally:
        owner.release()
        vault.clear_vault_cache()
    assert not update_lock.checkout_lock_held(root)
