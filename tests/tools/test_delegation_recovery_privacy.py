"""Restart hints authenticate the owning profile's encrypted transcript."""
from pathlib import Path

import pytest

from hermes_security import vault
from tools.async_delegation_recovery_hints import transcript_tail
from tools.delegation_live_log import create_live_transcripts


@pytest.fixture
def private_log(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(home, "synthetic recovery test password")
    vault.unlock(home, "synthetic recovery test password")
    _, writers, paths = create_live_transcripts([{"goal": "private recovery goal"}], home=home)
    assert len(paths) == 1 and writers[0] is not None
    yield home, writers[0], Path(paths[0])
    vault.clear_vault_cache()


def test_recovery_tail_authenticates_ciphertext_and_keeps_output_bounds(private_log):
    _, writer, path = private_log
    writer.assistant_text("private first line")
    writer.assistant_text("π" * 200 + " final synthetic result")
    assert b"final synthetic result" not in path.read_bytes()
    tail = transcript_tail(str(path), lines=1, chars=60)
    assert tail.endswith("final synthetic result")
    assert len(tail) <= 61 and "private first line" not in tail


def test_recovery_tail_refuses_a_locked_profile(private_log):
    home, _, path = private_log
    vault.lock_now(home)
    assert transcript_tail(str(path)) is None


def test_recovery_tail_refuses_unmanaged_plaintext(private_log, tmp_path):
    path = tmp_path / "outside.log"
    path.write_text("private unrelated plaintext", encoding="utf-8")
    assert transcript_tail(str(path)) is None
