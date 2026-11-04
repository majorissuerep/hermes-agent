"""Published legacy document_cache codecs remain recoverable during the media port."""
from __future__ import annotations

from pathlib import Path
import json
import secrets
import sqlite3
import tarfile

import pytest

from hermes_security import artifacts, frames, vault
from hermes_security.errors import VaultError
from hermes_security.migrate import repair_clobbered_state, scan_for_plaintext
from hermes_security.recovery_export import export_home


@pytest.fixture
def home(tmp_path, monkeypatch):
    selected = tmp_path / "owner"
    selected.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(selected))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(selected, "synthetic published document cache password")
    vault.unlock(selected, "synthetic published document cache password")
    yield selected
    vault.clear_vault_cache()


@pytest.mark.parametrize("extension", [".log", ".jsonl", ".env"])
def test_published_document_cache_codec_is_not_wrapped_or_reinterpreted(home, extension):
    relative = Path("document_cache") / ("doc_" + secrets.token_hex(16) + extension)
    path = home / relative
    path.parent.mkdir(mode=0o700)
    original = secrets.token_hex(24).encode() + b"\n"
    if extension == ".env":
        path.write_bytes(vault.get_vault(home).encrypt(original, purpose="env", relpath=relative.as_posix()))
    else:
        frames.append(path, original.rstrip(b"\n") if extension == ".jsonl" else original,
                      purpose="transcript" if extension == ".jsonl" else "log")
    path.chmod(0o600)
    before = path.read_bytes()
    repaired = repair_clobbered_state(home)
    assert repaired["unrecoverable"] == []
    assert path.read_bytes() == before
    assert repair_clobbered_state(home)["unrecoverable"] == []
    assert path.read_bytes() == before
    assert scan_for_plaintext(home) == []
    with pytest.raises(PermissionError, match="secure-vault export"):
        artifacts.read_bytes(path)
    output = home.parent / ("legacy-document" + extension + ".tar")
    export_home(home, output)
    with tarfile.open(output) as archive:
        assert archive.extractfile(relative.as_posix()).read() == original
    assert path.read_bytes() == before
    vault.lock_now(home)
    with pytest.raises(VaultError):
        export_home(home, home.parent / "locked.tar")
    assert path.read_bytes() == before


@pytest.mark.parametrize("extension", [".db", ".sqlite", ".sqlite3"])
def test_published_document_cache_database_keeps_its_export_contract(home, extension):
    from hermes_security.sqlite import connect

    relative = Path("document_cache") / ("doc_" + secrets.token_hex(16) + extension)
    path = home / relative
    path.parent.mkdir(mode=0o700)
    original = secrets.token_hex(24)
    connection = connect(path)
    try:
        connection.execute("CREATE TABLE old_media (body TEXT)")
        connection.execute("INSERT INTO old_media VALUES (?)", (original,))
        connection.commit()
    finally:
        connection.close()
    before = path.read_bytes()
    repaired = repair_clobbered_state(home)
    assert repaired["unrecoverable"] == []
    assert path.read_bytes() == before
    assert repair_clobbered_state(home)["unrecoverable"] == []
    assert path.read_bytes() == before
    assert scan_for_plaintext(home) == []
    output = home.parent / "legacy-database.tar"
    export_home(home, output)
    with tarfile.open(output) as archive:
        exported = archive.extractfile(relative.as_posix()).read()
    assert exported.startswith(b"SQLite format 3\x00")
    # This is the deliberately requested offline export, never a runtime reader.
    with sqlite3.connect(":memory:") as plain:
        plain.deserialize(exported)
        assert plain.execute("SELECT body FROM old_media").fetchall() == [(original,)]
    assert path.read_bytes() == before


@pytest.mark.parametrize("extension", [".log", ".jsonl", ".env", ".db", ".sqlite", ".sqlite3"])
def test_reserved_legacy_codecs_never_fall_back_to_the_task_backend(home, monkeypatch, extension):
    from hermes_security.sqlite import _sqlcipher_module
    from tools import file_tools

    path = home / "document_cache" / ("doc_" + secrets.token_hex(16) + extension)
    path.parent.mkdir(mode=0o700)
    path.write_bytes(secrets.token_bytes(128))
    path.chmod(0o600)
    before = path.read_bytes()
    monkeypatch.setattr(file_tools, "_get_file_ops", lambda *_: pytest.fail("reserved media reached raw backend"))
    result = json.loads(file_tools.read_file_tool(str(path), task_id="legacy-private-media"))
    assert "secure-vault export" in result["error"]
    assert artifacts.is_artifact_path(path)
    assert path.read_bytes() == before
    output = home.parent / "unknown-format.tar"
    with pytest.raises((VaultError, _sqlcipher_module().DatabaseError)):
        export_home(home, output)
    assert not output.exists() and path.read_bytes() == before


@pytest.mark.parametrize("extension", [".log", ".jsonl", ".env"])
def test_damaged_published_media_is_not_rewritten_or_exported(home, extension):
    relative = Path("document_cache") / ("doc_" + secrets.token_hex(16) + extension)
    path = home / relative
    path.parent.mkdir(mode=0o700)
    if extension == ".env":
        path.write_bytes(vault.get_vault(home).encrypt(secrets.token_bytes(40), purpose="env", relpath=relative.as_posix()))
    else:
        frames.append(path, secrets.token_bytes(40), purpose="log" if extension == ".log" else "transcript")
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 1
    path.write_bytes(raw)
    path.chmod(0o600)
    before = path.read_bytes()
    report = repair_clobbered_state(home)
    assert report["unrecoverable"]
    assert path.read_bytes() == before
    output = home.parent / "damaged-format.tar"
    with pytest.raises(VaultError):
        export_home(home, output)
    assert not output.exists() and path.read_bytes() == before
