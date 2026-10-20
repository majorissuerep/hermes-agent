"""Legacy vault commands preserve the canonical unlock gate after passphrase wrapping."""
import base64
import json
import os
from types import SimpleNamespace

import pytest

from hermes_security import vault
from hermes_security.passphrase import change_passphrase


@pytest.fixture
def wrapped_home(tmp_path, monkeypatch):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override

    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    home = tmp_path / "owner"
    home.mkdir()
    vault.init_vault(home, "original synthetic password")
    vault.unlock(home, "original synthetic password")
    private, public = vault.generate_keypair()
    vault.add_key_slot(home, public_key_raw=public, unlocked_vault=vault.get_vault(home))
    change_passphrase(home, "replacement synthetic password")
    token = set_hermes_home_override(home)
    try:
        yield home, private
    finally:
        reset_hermes_home_override(token)
        vault.lock_now(home)


@pytest.mark.parametrize("authority", ["unlocked", "fd", "key-file", "tty"])
def test_remove_key_uses_existing_authority_or_one_canonical_unlock(wrapped_home, tmp_path, monkeypatch, authority):
    from hermes_cli import vault_cmd
    from hermes_security.handoff import prepare_child_key

    home, private = wrapped_home
    prompts, fd = [], None
    monkeypatch.delenv("HERMES_VAULT_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("HERMES_SESSION_VAULT", raising=False)
    monkeypatch.delenv("HERMES_VAULT_KEY_FD", raising=False)

    def password():
        prompts.append(True)
        assert authority == "tty", "an inherited or cached authority was prompted again"
        return "replacement synthetic password"

    monkeypatch.setattr(vault_cmd, "_password_from_env_or_prompt", password)
    if authority == "fd":
        fd, additions = prepare_child_key()
        monkeypatch.setenv("HERMES_VAULT_KEY_FD", additions["HERMES_VAULT_KEY_FD"])
    elif authority == "key-file":
        keyfile = tmp_path / "synthetic-private-key"
        keyfile.write_bytes(base64.urlsafe_b64encode(private))
        keyfile.chmod(0o600)
        monkeypatch.setenv("HERMES_VAULT_PRIVATE_KEY", str(keyfile))
    if authority != "unlocked":
        vault.lock_now(home)
    try:
        assert vault_cmd.cmd_vault(SimpleNamespace(vault_command="remove-key", slot=0)) == 0
        assert json.loads((home / ".hermes-vault").read_text())["key_slots"] == []
        assert len(prompts) == (1 if authority == "tty" else 0)
        vault.lock_now(home)
        assert vault.unlock(home, "replacement synthetic password")
    finally:
        if fd is not None:
            # The canonical gate consumes its one-shot FD; a pre-gate failure leaves it owned here.
            try:
                os.close(fd)
            except OSError:
                pass


def test_status_reports_wrapped_passphrase_kdf_without_unlocking_or_credential_output(wrapped_home, monkeypatch, capsys):
    from hermes_cli import vault_cmd

    home, _ = wrapped_home
    meta = json.loads((home / ".hermes-vault").read_text())
    vault.lock_now(home)
    assert vault_cmd.cmd_vault(SimpleNamespace(vault_command="status")) == 0
    output = capsys.readouterr().out
    assert "scrypt (n=1024, r=8, p=1)" in output
    assert "v2" in output
    assert "replacement synthetic password" not in output
    for name in ("sealed", "nonce"):
        assert meta["passphrase"][name] not in output
    assert meta["authentication"] not in output
    assert not vault.is_unlocked(home)
