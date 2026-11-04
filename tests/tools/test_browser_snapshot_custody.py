"""Private browser trees remain encrypted and recoverable through existing file tools."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hermes_security import artifacts, vault
from hermes_security.migrate import repair_clobbered_state, scan_for_plaintext
from tools.browser_tool_snapshot import _store_full_snapshot, _truncate_snapshot


@pytest.fixture
def owner(tmp_path, monkeypatch):
    home = tmp_path / "owner"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(home, "synthetic browser tree password")
    vault.unlock(home, "synthetic browser tree password")
    yield home
    vault.clear_vault_cache()


def test_snapshot_paging_and_search_keep_original_private_content_off_disk(owner, monkeypatch):
    from tools.file_tools import read_file_tool, search_tool

    monkeypatch.setenv("TERMINAL_ENV", "ssh")
    body = "\n".join(f'- item "private-page-74fe line {i}" [ref=e{i}]' for i in range(80))
    stored = Path(_store_full_snapshot(body))
    assert b"private-page-74fe" not in stored.read_bytes()
    assert stored.is_relative_to(owner / "artifacts" / "browser-snapshots")
    assert artifacts.read_text(stored) == body
    assert _store_full_snapshot(body) == str(stored)
    footer = _truncate_snapshot(body, max_chars=500)
    assert f'read_file path="{stored}"' in footer
    paged = json.loads(read_file_tool(str(stored), offset=71, limit=3, task_id="tree-paging"))
    assert "line 70" in paged["content"] and "ref=e72" in paged["content"]
    found = json.loads(search_tool("ref=e79", path=str(stored), task_id="tree-search"))
    assert "ref=e79" in json.dumps(found)
    assert "profile-owned private artifact" in paged["source"]
    assert not (owner / "cache" / "web").exists()
    assert scan_for_plaintext(owner) == []
    vault.lock_now(owner)
    refused = json.loads(read_file_tool(str(stored), task_id="tree-locked"))
    assert refused.get("error") and "private-page-74fe" not in json.dumps(refused)


@pytest.mark.parametrize("branch", ["cache/web", "web_cache"])
def test_explicit_repair_seals_old_browser_tree_and_keeps_public_cache_bytes(owner, branch):
    body = b"private legacy browser tree c72a\n- button [ref=e93]"
    digest = hashlib.sha256(body).hexdigest()[:10]
    path = owner / branch / f"browser-snapshot-{digest}.txt"
    path.parent.mkdir(parents=True)
    path.write_bytes(body)
    public = owner / "cache" / "web" / "ordinary-extract.txt"
    public.parent.mkdir(parents=True, exist_ok=True)
    public.write_bytes(b"public extracted document")
    assert path.relative_to(owner).as_posix() in scan_for_plaintext(owner)
    result = repair_clobbered_state(owner)
    assert result["unrecoverable"] == []
    assert body not in path.read_bytes() and artifacts.read_bytes(path) == body
    assert public.read_bytes() == b"public extracted document"
    assert scan_for_plaintext(owner) == []


@pytest.mark.platforms("posix")
@pytest.mark.require_symlinks
def test_snapshot_symlink_and_missing_unlock_never_write_plaintext(owner):
    body = "private tree destination ce51\n- link [ref=e9]"
    digest = hashlib.sha256(body.encode()).hexdigest()[:10]
    target = owner / "artifacts" / "browser-snapshots" / f"browser-snapshot-{digest}.txt"
    target.parent.mkdir(parents=True)
    victim = owner.parent / "outside.txt"
    victim.write_text("untouched", encoding="utf-8")
    target.symlink_to(victim)
    assert _store_full_snapshot(body) is None
    assert target.is_symlink() and victim.read_text(encoding="utf-8") == "untouched"
    target.unlink()
    vault.lock_now(owner)
    assert _store_full_snapshot(body) is None
    assert not target.exists()
    result = _truncate_snapshot(body * 50, max_chars=500)
    assert "unavailable" in result and "encrypted" in result
