"""Private terminal/tool artifacts stay encrypted and recover through public file tools."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from hermes_security import artifacts, vault
from hermes_security.errors import VaultLockedError
from tools import file_tools, terminal_tool_result, tool_result_storage
from tools.environments.base_output import _BoundedOutputCollector


@pytest.fixture
def home(tmp_path, monkeypatch):
    selected = tmp_path / "owner"
    selected.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(selected))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(selected, "synthetic output custody password")
    vault.unlock(selected, "synthetic output custody password")
    yield selected
    vault.clear_vault_cache()


def test_foreground_spill_is_sealed_before_and_after_redaction(home):
    path = home / "cache" / "terminal-output" / "out-canary.log"
    text = "synthetic private terminal canary d1a73\n" * 8
    collector = _BoundedOutputCollector(24, spill_path=path)
    collector.append(text[:40])
    collector.append(text[40:])
    assert collector.close_spill() == str(path)
    assert b"synthetic private terminal canary" not in path.read_bytes()
    metadata = dict(terminal_tool_result._redact_spill_file(str(path), len(text), "synthetic"))
    assert metadata["full_output_path"] == str(path)
    assert b"synthetic private terminal canary" not in path.read_bytes()


def test_generic_artifact_sealed_paged_and_searched_without_backend_upload(home, monkeypatch):
    text = "\n".join(f"row {n}: synthetic private result canary 2d140" for n in range(80))
    remote = Mock()
    remote.execute.side_effect = AssertionError("private output must never be uploaded")
    result = tool_result_storage.maybe_persist_tool_result(text, "synthetic", "heldout", env=remote, threshold=1)
    saved = tool_result_storage.extract_persisted_path(result)
    assert saved is not None
    path = Path(saved)
    assert path.is_relative_to(home)
    assert b"synthetic private result canary" not in path.read_bytes()
    monkeypatch.setattr(file_tools, "_get_file_ops", lambda *_: (_ for _ in ()).throw(
        AssertionError("explicit owner artifact must not execute on a task backend")))
    page = json.loads(file_tools.read_file_tool(saved, offset=31, limit=2, task_id="private-page"))
    assert not page.get("error")
    assert "row 30:" in page["content"] and "row 31:" in page["content"]
    assert "row 32:" not in page["content"]
    found = json.loads(file_tools.search_tool("row 37:", path=saved, task_id="private-search"))
    assert not found.get("error")
    assert found["total_count"] == 1
    assert "row 37:" in found["matches"][0]["content"]
    remote.execute.assert_not_called()


def test_generic_local_result_never_persists_plaintext(home):
    text = "synthetic private local result canary 9c341\n" * 80
    result = tool_result_storage.maybe_persist_tool_result(text, "synthetic", "local-heldout", threshold=1)
    saved = tool_result_storage.extract_persisted_path(result)
    assert saved is not None
    assert b"synthetic private local result canary" not in Path(saved).read_bytes()


def test_stream_original_custody_cannot_reopen_after_lock_and_reunlock(home):
    target = home / "artifacts" / "terminal-output" / "held.log"
    sink = artifacts.open_text(target, home=home)
    sink.write("synthetic original generation\n" * 200)
    before = target.read_bytes()
    vault.lock_now(home)
    vault.unlock(home, "synthetic output custody password")
    try:
        with pytest.raises(VaultLockedError):
            sink.write("synthetic forbidden revived write\n")
        assert target.read_bytes() == before
    finally:
        sink.close()
    artifacts.write_bytes(home / "artifacts" / "tool-results" / "new.txt", b"fresh generation", home=home)


@pytest.mark.require_symlinks
@pytest.mark.parametrize("component", ["file", "directory"])
def test_private_publication_refuses_symlink_without_touching_target(home, tmp_path, component):
    target = home / "artifacts" / "tool-results" / "held.txt"
    victim = tmp_path / "victim"
    if component == "directory":
        victim.mkdir()
        (home / "artifacts").mkdir()
        target.parent.symlink_to(victim, target_is_directory=True)
    else:
        target.parent.mkdir(parents=True)
        victim.write_bytes(b"synthetic original victim")
        target.symlink_to(victim)
    with pytest.raises(OSError):
        artifacts.write_bytes(target, b"synthetic forbidden redirect", home=home, overwrite=True)
    if component == "directory":
        assert list(victim.iterdir()) == []
    else:
        assert victim.read_bytes() == b"synthetic original victim"


def test_foreign_artifact_refused_and_browser_image_stays_binary(home, tmp_path, monkeypatch):
    image = home / "artifacts" / "browser" / "screen.png"
    artifacts.write_bytes(image, b"\x89PNG\r\n\x1a\nsynthetic image canary", home=home)
    monkeypatch.setattr(file_tools, "_get_file_ops", lambda *_: (_ for _ in ()).throw(
        AssertionError("no backend fallback for private artifacts")))
    read = json.loads(file_tools.read_file_tool(str(image), task_id="private-image"))
    assert read["is_image"] and read["is_binary"]
    assert "vision_analyze" in read["hint"]
    assert "content" not in read or not read["content"]
    foreign = tmp_path / "foreign"
    vault.init_vault(foreign, "synthetic foreign password")
    vault.unlock(foreign, "synthetic foreign password")
    other = foreign / "artifacts" / "tool-results" / "other.txt"
    artifacts.write_bytes(other, b"synthetic foreign plaintext", home=foreign)
    refused = json.loads(file_tools.read_file_tool(str(other), task_id="private-foreign"))
    assert "different profile" in refused["error"]
    assert "synthetic foreign plaintext" not in json.dumps(refused)


def test_canonical_write_without_vault_never_falls_back_to_plaintext(tmp_path):
    home = tmp_path / "uninitialized"
    home.mkdir()
    target = home / "artifacts" / "tool-results" / "new.txt"
    with pytest.raises(VaultLockedError):
        artifacts.write_bytes(target, b"synthetic forbidden plaintext", home=home)
    assert not target.exists()


def test_collector_archive_failure_preserves_exit_and_reports_error(home):
    from tools.environments.base_output import _finalize_wait_result

    vault.lock_now(home)
    collector = _BoundedOutputCollector(16, home / "artifacts" / "terminal-output" / "locked.log")
    collector.append("synthetic bounded output" * 10)
    result = _finalize_wait_result(collector, "head and tail", 7)
    assert result["returncode"] == 7 and result["output"] == "head and tail"
    assert "output_archive_error" in result and "full_output_path" not in result


@pytest.mark.platforms("linux")
def test_public_terminal_full_output_recovers_without_plaintext_state(home, monkeypatch):
    from hermes_constants import hermes_home_key
    from tools import tool_output_limits
    from tools.terminal_tool import terminal_tool

    monkeypatch.setattr(tool_output_limits, "_cached_limits", {hermes_home_key(): {
        "max_bytes": 500, "max_lines": 2000, "max_line_length": 2000,
    }})
    result = json.loads(terminal_tool(
        "python3 -c \"[print('synthetic public terminal canary a7f45', i) for i in range(100)]\"",
        task_id="public-private-output"))
    assert result["exit_code"] == 0
    saved = result["full_output_path"]
    page = json.loads(file_tools.read_file_tool(saved, offset=41, limit=2, task_id="public-private-output"))
    assert "synthetic public terminal canary a7f45 40" in page["content"]
    assert "synthetic public terminal canary a7f45 42" not in page["content"]
    found = json.loads(file_tools.search_tool("a7f45 73$", path=saved, task_id="public-private-output"))
    assert found["total_count"] == 1
    assert "a7f45 73" in found["matches"][0]["content"]
    canary = b"synthetic public terminal canary a7f45"
    assert all(canary not in p.read_bytes() for p in home.rglob("*") if p.is_file())


def test_tiny_chunks_keep_the_archive_bounded_and_recover_the_full_cap(home, monkeypatch):
    path = home / "artifacts" / "terminal-output" / "tiny-chunks.log"
    monkeypatch.setattr(_BoundedOutputCollector, "_SPILL_CAP_CHARS", 256)
    collector = _BoundedOutputCollector(32, spill_path=path)
    text = "synthetic tiny chunk stream " * 20
    for char in text:
        collector.append(char)
    assert collector.close_spill() == str(path)
    recovered = artifacts.read_text(path)
    assert recovered.startswith(text[:256])
    assert "spill capped" in recovered
    assert len(recovered) < 400 and path.stat().st_size < 1000


def test_private_search_modes_keep_exact_counts_and_glob_pagination(home):
    root = home / "artifacts" / "tool-results"
    for name in ("a.txt", "b.txt"):
        artifacts.write_bytes(root / name, b"synthetic match\n" * 20)
    counted = json.loads(file_tools.search_tool("synthetic", path=str(root), output_mode="count", limit=1,
                                               task_id="private-counts"))
    assert counted["total_count"] == 40 and sorted(counted["counts"].values()) == [20, 20]
    files = json.loads(file_tools.search_tool("*.txt", path=str(root), target="files", limit=1, offset=1,
                                             task_id="private-glob"))
    assert files["total_count"] == 2 and files["files"] == [str(root / "b.txt")]


@pytest.mark.require_symlinks
def test_explicit_home_alias_keeps_child_symlink_refusal(home, tmp_path):
    alias = tmp_path / "owner-alias"
    alias.symlink_to(home, target_is_directory=True)
    target = alias / "artifacts" / "tool-results" / "alias.txt"
    published = artifacts.write_bytes(target, b"synthetic home alias", home=alias)
    assert published == home / "artifacts" / "tool-results" / "alias.txt"
    assert artifacts.read_bytes(target, home=alias) == b"synthetic home alias"


@pytest.mark.parametrize("family,suffix", [("tool-results", ".txt"), ("terminal-output", ".log")])
def test_accepted_plaintext_maximum_roundtrips_and_larger_payload_refuses(home, monkeypatch, family, suffix):
    monkeypatch.setattr(artifacts, "_MAX_BYTES", 64)
    target = home / "artifacts" / family / ("boundary" + suffix)
    payload = bytes(range(64))
    artifacts.write_bytes(target, payload, home=home)
    assert artifacts.read_bytes(target, home=home) == payload
    before = target.read_bytes()
    with pytest.raises(ValueError):
        artifacts.write_bytes(target, payload + b"x", home=home, overwrite=True)
    assert target.read_bytes() == before


@pytest.mark.parametrize("family", ["artifacts/browser", "cache/screenshots", "browser_screenshots"])
def test_image_families_do_not_become_text_reads_or_searches_by_renaming(home, family):
    image = home / family / "mislabeled.txt"
    artifacts.write_bytes(image, b"synthetic private image body marker", home=home)
    result = json.loads(file_tools.read_file_tool(str(image), task_id="mislabeled-image-read"))
    assert result["is_binary"] and "synthetic private image body" not in json.dumps(result)
    found = json.loads(file_tools.search_tool("synthetic private image", path=str(image),
                                             task_id="mislabeled-image-search"))
    assert found["total_count"] == 0 and "synthetic private image body" not in json.dumps(found)
