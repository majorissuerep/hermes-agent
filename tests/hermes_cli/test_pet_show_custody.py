"""Fixture-only command-helper contract: retained selection must retain original custody.

No public CLI process, terminal or app is launched. The renderer/crypto are real;
command arguments, terminal mode/output and a single animation iteration are fixture seams.
"""
from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from PIL import Image

from agent.pet import render, store
from hermes_cli import pets
from hermes_security import vault
from hermes_security.errors import VaultError


def test_show_does_not_substitute_a_fresh_unlock_for_its_retained_selection(tmp_path, monkeypatch):
    home = tmp_path / "owner"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    password = "synthetic retained pet show password"
    vault.init_vault(home, password)
    vault.unlock(home, password)
    selected = store.register_local_pet(Image.new("RGBA", (384, 1872), "navy"), slug="unit-animal")
    monkeypatch.setattr(pets, "_pet_config", dict)
    monkeypatch.setattr(render, "resolve_mode", lambda *_args, **_kwargs: "unicode")
    output = io.StringIO()
    monkeypatch.setattr("sys.stdout", output)
    args = SimpleNamespace(slug=selected.slug, mode="unicode", scale=1, state="idle", cycle=False, once=True)

    def one_fixture_frame(_delay):
        raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", one_fixture_frame)
    try:
        vault.lock_now(home)
        vault.unlock(home, password)
        monkeypatch.setattr(store, "resolve_active_pet", lambda *_args: selected)
        with pytest.raises(VaultError):
            pets._cmd_show(args)
        assert output.getvalue() == ""
        fresh = store.load_pet(selected.slug)
        monkeypatch.setattr(store, "resolve_active_pet", lambda *_args: fresh)
        assert pets._cmd_show(args) == 0 and output.getvalue()
        assert vault.is_unlocked(home)
        before = output.getvalue()
        other = tmp_path / "other-profile"
        other.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(other))
        vault.init_vault(other, "synthetic other profile password")
        vault.unlock(other, "synthetic other profile password")
        with pytest.raises(PermissionError, match="different profile"):
            pets._cmd_show(args)
        assert output.getvalue() == before and vault.is_unlocked(home) and vault.is_unlocked(other)
    finally:
        vault.clear_vault_cache()
