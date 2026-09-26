"""At-rest scans must classify unverified bytes as failures, never clean state."""
import pytest

from hermes_security import frames, io, migrate, vault


@pytest.mark.linux_only
def test_scan_does_not_skip_unreadable_subtrees(tmp_path):
    import os
    if os.geteuid() == 0:
        pytest.skip("root bypasses directory mode denial")
    home = tmp_path / "vault"
    vault.init_vault(home, "synthetic-scan-test")
    vault.unlock(home, "synthetic-scan-test")
    blocked = home / "blocked"
    blocked.mkdir()
    (blocked / "state.json").write_text('{"secret":"synthetic"}')
    blocked.chmod(0)
    try:
        assert "blocked" in migrate.scan_for_plaintext(home)
    finally:
        blocked.chmod(0o700)
        vault.clear_vault_cache()


def test_scan_missing_root_is_not_clean(tmp_path):
    assert migrate.scan_for_plaintext(tmp_path / "missing")


@pytest.mark.parametrize("relative,raw", [
    ("sessions/plain.jsonl", b'{"role":"user","content":"canary"}\n'),
    ("sessions/bom.jsonl", b'\xef\xbb\xbf{"x":1}\n'),
    ("logs/broken.log", b'\x00\x00\x10\x00partial'),
    ("state.db", b'random-not-a-database'),
    ("config.yaml", b'HRMVAULT\x00not-an-authenticated-envelope'),
])
def test_scan_rejects_plaintext_and_unverifiable_state(tmp_path, relative, raw):
    home = tmp_path / "home"
    vault.init_vault(home, "synthetic-scan")
    vault.unlock(home, "synthetic-scan")
    path = home / relative
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_bytes(raw)
    try:
        assert relative in migrate.scan_for_plaintext(home)
    finally:
        vault.clear_vault_cache()


def test_scan_authenticates_envelopes_and_frames_and_reports_read_errors(tmp_path, monkeypatch):
    home = tmp_path / "home"
    vault.init_vault(home, "synthetic-scan")
    vault.unlock(home, "synthetic-scan")
    stream = home / "stream.jsonl"
    frames.append(stream, b'{"x":1}', purpose="transcript")
    (home / "empty.log").touch()
    io.write_text(home / "config.yaml", "model: synthetic\n", purpose="config")
    try:
        assert migrate.scan_for_plaintext(home) == []
        original = type(stream).open

        def denied(path, *args, **kwargs):
            if path == stream:
                raise PermissionError("synthetic denied read")
            return original(path, *args, **kwargs)

        with monkeypatch.context() as patcher:
            patcher.setattr(type(stream), "open", denied)
            assert "stream.jsonl" in migrate.scan_for_plaintext(home)
        vault.clear_vault_cache()
        assert "config.yaml" in migrate.scan_for_plaintext(home)
    finally:
        vault.clear_vault_cache()
