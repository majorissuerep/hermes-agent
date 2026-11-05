"""The already-published format1 E ciphertext remains readable without rewriting it.

This public synthetic vector was emitted by the independent frozen72a writer,
before the shared tree engine extraction. It contains no real user data or raw key.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from hermes_security import browser_profile_archive as archive
from hermes_security import vault


FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures' / 'browser_profile_archive_v1.json'
PASSWORD = 'synthetic E archive interoperability fixture'


def test_published_e_archive_decrypts_without_ciphertext_or_metadata_rewrite(tmp_path, monkeypatch):
    vector = json.loads(FIXTURE.read_text())
    home = tmp_path / 'owner'
    home.mkdir(mode=0o700)
    home.chmod(0o700)
    relative = Path(vector['relative'])
    cipher = home / relative
    cipher.parent.mkdir(mode=0o700)
    cipher.parent.chmod(0o700)
    metadata = home / '.hermes-vault'
    metadata.write_bytes(base64.b64decode(vector['vault_metadata_b64'], validate=True))
    metadata.chmod(0o600)
    cipher.write_bytes(base64.b64decode(vector['archive_b64'], validate=True))
    cipher.chmod(0o600)
    before = metadata.read_bytes(), cipher.read_bytes()
    assert tuple(hashlib.sha256(data).hexdigest() for data in before) == (
        vector['metadata_sha256'], vector['cipher_sha256'])
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(vault, '_SCRYPT_N', vector['scrypt_n'])
    try:
        vault.unlock(home, PASSWORD)
        custody = archive.capture_custody(home)
        restored = tmp_path / 'restored'
        restored.mkdir(mode=0o700)
        summary = archive.replay_archive(home, relative, custody=custody, slot=vector['slot'],
                                         digest=vector['identity_digest'], target=restored)
        assert (summary.entries, summary.bytes) == (vector['entries'], vector['plaintext_bytes'])
        assert {path.relative_to(restored).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in restored.rglob('*') if path.is_file()} == vector['expected_file_hashes']
        assert (restored / 'empty-directory').is_dir()
        assert summary == archive.tree_digest(restored)
        assert (metadata.read_bytes(), cipher.read_bytes()) == before
        assert sorted(path.relative_to(home).as_posix() for path in home.rglob('*') if path.is_file()) == [
            '.hermes-vault', relative.as_posix()]
    finally:
        vault.clear_vault_cache()
