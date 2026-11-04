"""Tests for tools/tool_result_storage.py -- 3-layer tool result persistence."""

import json
import os
from pathlib import Path

import pytest
from unittest.mock import MagicMock, patch

from hermes_security import artifacts, vault

from tools.budget_config import (
    DEFAULT_PREVIEW_SIZE_CHARS,
    BudgetConfig,
)
from tools.tool_result_storage import (
    PERSISTED_OUTPUT_TAG,
    PERSISTED_OUTPUT_CLOSING_TAG,
    _build_persisted_message,
    _pageable_text,
    _safe_result_filename,
    cleanup_spillover_cache,
    enforce_turn_budget,
    generate_preview,
    get_spillover_dir,
    maybe_persist_tool_result,
)


@pytest.fixture(autouse=True)
def sealed_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(home, "synthetic result storage password")
    vault.unlock(home, "synthetic result storage password")
    monkeypatch.setattr("tools.tool_result_storage._spillover_pruned_homes", set())
    yield home
    vault.clear_vault_cache()


class TestVerifiedArchive:
    @pytest.mark.parametrize("complete", [False, True])
    def test_byte_exact_recovery_is_required(self, monkeypatch, complete):
        from tools.tool_result_storage import _write_to_spillover

        content = "héllo wörld ✓" * 10
        original = artifacts.read_bytes
        if not complete:
            monkeypatch.setattr(artifacts, "read_bytes", lambda *a, **kw: original(*a, **kw)[:-1])
        path = _write_to_spillover(content, "byte-exact.txt")
        assert (path is not None) is complete
        assert (get_spillover_dir() / "byte-exact.txt").exists() is complete
        if complete:
            assert artifacts.read_text(path) == content

# ── generate_preview ──────────────────────────────────────────────────

class TestGeneratePreview:
    def test_short_content_unchanged(self):
        text = "short result"
        preview, has_more = generate_preview(text)
        assert preview == text
        assert has_more is False

    def test_exact_boundary(self):
        text = "x" * DEFAULT_PREVIEW_SIZE_CHARS
        preview, has_more = generate_preview(text)
        assert preview == text
        assert has_more is False

# ── _write_to_sandbox ─────────────────────────────────────────────────



class TestSafeResultFilename:
    def test_preserves_normal_tool_call_id(self):
        assert _safe_result_filename("tc_456") == "tc_456.txt"

    def test_replaces_path_and_shell_metacharacters(self):
        filename = _safe_result_filename("../outside/$(whoami);x")
        assert filename.startswith("outside_whoami_x_")
        assert filename.endswith(".txt")
        assert "/" not in filename
        assert "$" not in filename
        assert ";" not in filename

# ── _build_persisted_message ──────────────────────────────────────────

class TestBuildPersistedMessage:
    def test_structure(self):
        msg = _build_persisted_message(
            preview="first 100 chars...",
            has_more=True,
            original_size=50_000,
            file_path="/tmp/hermes-results/test123.txt",
        )
        assert msg.startswith(PERSISTED_OUTPUT_TAG)
        assert msg.endswith(PERSISTED_OUTPUT_CLOSING_TAG)
        assert "/tmp/hermes-results/test123.txt" in msg
        assert "read_file" in msg
        assert "first 100 chars..." in msg
        assert "..." in msg  # has_more indicator

# ── maybe_persist_tool_result ─────────────────────────────────────────

class TestMaybePersistToolResult:
    def test_below_threshold_returns_unchanged(self):
        content = "small result"
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_123",
            env=None,
            threshold=50_000,
        )
        assert result == content

    def test_above_threshold_with_env_persists(self):
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        content = "x" * 60_000
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_456",
            env=env,
            threshold=30_000,
        )
        assert PERSISTED_OUTPUT_TAG in result
        assert "tc_456.txt" in result
        assert len(result) < len(content)

    def test_persists_full_content_as_is(self):
        """Content is persisted verbatim — no JSON extraction."""
        import json
        env = MagicMock()
        # A task backend's capabilities do not change private owner storage.
        env.execute.side_effect = [
            {"output": "", "returncode": 1},
            {"output": "", "returncode": 0},
            {"output": "", "returncode": 1},  # wc -c size probe: no answer
        ]
        env.get_temp_dir.return_value = ""
        raw = "line1\nline2\n" * 5_000
        content = json.dumps({"output": raw, "exit_code": 0, "error": None})
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_json",
            env=env,
            threshold=30_000,
        )
        assert PERSISTED_OUTPUT_TAG in result
        # Content is delivered through stdin (no longer embedded in the
        # command string — see test_large_content_via_stdin for why).
        assert artifacts.read_text(get_spillover_dir() / "tc_json.txt") == content
        env.execute.assert_not_called()

    def test_tool_use_id_cannot_escape_storage_dir(self):
        env = MagicMock()
        # Caller-controlled ids must remain a single safe owner-archive filename.
        env.execute.side_effect = [
            {"output": "", "returncode": 1},
            {"output": "", "returncode": 0},
            {"output": "", "returncode": 1},  # wc -c size probe: no answer
        ]
        env.get_temp_dir.return_value = ""
        content = "x" * 60_000
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="../outside/$(whoami);x",
            env=env,
            threshold=30_000,
        )
        from tools.tool_result_storage import extract_persisted_path
        target = extract_persisted_path(result)
        assert target is not None
        assert str(get_spillover_dir() / "outside_whoami_x_") in target
        assert Path(target).parent == get_spillover_dir()
        assert "/../" not in target and "$(whoami)" not in target and ";" not in target
        assert artifacts.read_text(target) == content
        env.execute.assert_not_called()

    def test_threshold_zero_forces_persist(self):
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        content = "even short content"
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_zero",
            env=env,
            threshold=0,
        )
        # Any non-empty content with threshold=0 should be persisted
        assert PERSISTED_OUTPUT_TAG in result

# ── enforce_turn_budget ───────────────────────────────────────────────

class TestEnforceTurnBudget:
    def test_under_budget_no_changes(self):
        msgs = [
            {"role": "tool", "tool_call_id": "t1", "content": "small"},
            {"role": "tool", "tool_call_id": "t2", "content": "also small"},
        ]
        result = enforce_turn_budget(msgs, env=None, config=BudgetConfig(turn_budget=200_000))
        assert result[0]["content"] == "small"
        assert result[1]["content"] == "also small"

    def test_medium_result_regression(self):
        """6 results of 42K chars each (252K total) — each under 100K default
        threshold but aggregate exceeds 200K budget. L3 should persist."""
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        msgs = [
            {"role": "tool", "tool_call_id": f"t{i}", "content": "x" * 42_000}
            for i in range(6)
        ]
        enforce_turn_budget(msgs, env=env, config=BudgetConfig(turn_budget=200_000))
        # At least some results should be persisted to get under 200K
        persisted_count = sum(
            1 for m in msgs if PERSISTED_OUTPUT_TAG in m["content"]
        )
        assert persisted_count >= 2  # Need to shed at least ~52K

    def test_empty_messages(self):
        result = enforce_turn_budget([], env=None, config=BudgetConfig(turn_budget=200_000))
        assert result == []

    def test_multimodal_results_count_their_text_and_spill_it(self):
        """Image-bearing results are part lists: the aggregate counts their text characters (not the number of
        parts) and spills the big text part, keeping images and the short notes inline."""
        img = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}
        msgs = [{"role": "tool", "tool_call_id": f"mm{i}",
                 "content": [{"type": "text", "text": "x" * 60_000}, {"type": "text", "text": "scale note"}, img]}
                for i in range(6)]
        enforce_turn_budget(msgs, env=None, config=BudgetConfig(turn_budget=200_000))
        assert sum(PERSISTED_OUTPUT_TAG in m["content"][0]["text"] for m in msgs) >= 2
        assert all(m["content"][1]["text"] == "scale note" and m["content"][2] == img for m in msgs)

# ── Per-tool threshold integration ────────────────────────────────────

class TestPerToolThresholds:
    """Verify registry wiring for per-tool thresholds."""

    def test_read_file_registry_cap_is_finite(self):
        """read_file must keep a finite registry cap (Layer 2 safety net)."""
        from tools.registry import registry
        try:
            import tools.file_tools
            val = registry.get_max_result_size("read_file")
            # float('inf') disables the Layer 2 result-size guard.
            assert 0 < val < float("inf"), val
        except ImportError:
            pytest.skip("file_tools not importable in test env")

    def test_search_files_threshold(self):
        from tools.registry import registry
        try:
            import tools.file_tools
            val = registry.get_max_result_size("search_files")
            assert 0 < val < float("inf"), val
        except ImportError:
            pytest.skip("file_tools not importable in test env")

# ── Host-side spillover ($HERMES_HOME/cache/spillover) ────────────────

class TestSpillover:

    def test_env_none_persists_to_spillover(self):
        """No active sandbox env (MCP-only / cron session) must persist
        host-side instead of inline-truncating — the guglielmo bundle bug."""
        content = "x" * 60_000
        result = maybe_persist_tool_result(
            content=content,
            tool_name="tool_call",
            tool_use_id="tc_mcp_1",
            env=None,
            threshold=30_000,
        )
        assert PERSISTED_OUTPUT_TAG in result
        assert "could not be saved" not in result
        spill_file = get_spillover_dir() / "tc_mcp_1.txt"
        assert spill_file.exists()
        assert artifacts.read_text(spill_file) == content
        assert str(spill_file) in result

    def test_local_env_persists_to_spillover_not_sandbox(self):
        """LocalEnvironment routes host-side: no env.execute() shell-out."""
        from tools.environments.local import LocalEnvironment

        env = MagicMock(spec=LocalEnvironment)
        content = "y" * 60_000
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_local_1",
            env=env,
            threshold=30_000,
        )
        assert PERSISTED_OUTPUT_TAG in result
        assert (get_spillover_dir() / "tc_local_1.txt").exists()
        env.execute.assert_not_called()

    def test_remote_env_recovers_explicit_owner_artifact_without_upload(self):
        """Remote execution does not authorize copying private output to that backend."""
        env = MagicMock()  # not a LocalEnvironment
        env.execute.return_value = {"output": "", "returncode": 0}  # probe OK
        content = "z" * 60_000
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_remote_1",
            env=env,
            threshold=30_000,
        )
        assert PERSISTED_OUTPUT_TAG in result
        # Canonical host copy always exists now.
        assert (get_spillover_dir() / "tc_remote_1.txt").exists()
        # No backend command or upload is needed for an explicit owner artifact.
        env.execute.assert_not_called()

    def test_remote_backend_without_mount_still_uses_owner_archive(self):
        """Owner file tools recover the artifact even without a remote mount."""
        env = MagicMock()
        env.execute.side_effect = [
            {"output": "", "returncode": 1},  # probe: not readable
            {"output": "", "returncode": 0},  # cat > sandbox path
            {"output": "60000\n", "returncode": 0},  # wc -c verification
        ]
        env.get_temp_dir.return_value = "/tmp"
        content = "z" * 60_000
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_remote_2",
            env=env,
            threshold=30_000,
        )
        assert PERSISTED_OUTPUT_TAG in result
        assert str(get_spillover_dir() / "tc_remote_2.txt") in result
        env.execute.assert_not_called()
        # Host canonical copy exists regardless.
        assert (get_spillover_dir() / "tc_remote_2.txt").exists()

    def test_spillover_write_failure_falls_back_to_inline(self, monkeypatch):
        import tools.tool_result_storage as trs
        monkeypatch.setattr(trs, "_write_to_spillover", lambda *a, **k: None)
        content = "w" * 60_000
        result = maybe_persist_tool_result(
            content=content,
            tool_name="tool_call",
            tool_use_id="tc_fail_1",
            env=None,
            threshold=30_000,
        )
        assert "could not be saved" in result
        assert PERSISTED_OUTPUT_TAG not in result

    def test_cleanup_spillover_cache_removes_old_keeps_new(self):
        import time as _time

        spill_dir = get_spillover_dir()
        spill_dir.mkdir(parents=True, exist_ok=True)
        old = spill_dir / "old.txt"
        new = spill_dir / "new.txt"
        artifacts.write_bytes(old, b"old")
        artifacts.write_bytes(new, b"new")
        stale = _time.time() - (48 * 3600)
        os.utime(old, (stale, stale))

        removed = cleanup_spillover_cache(max_age_hours=24)

        assert removed == 1
        assert not old.exists()
        assert new.exists()

    def test_cleanup_missing_dir_returns_zero(self):
        assert cleanup_spillover_cache() == 0

    def test_first_spill_prunes_expired_files(self):
        """The once-per-process prune fires on the first host-side spill."""
        import time as _time

        spill_dir = get_spillover_dir()
        spill_dir.mkdir(parents=True, exist_ok=True)
        old = spill_dir / "ancient.txt"
        artifacts.write_bytes(old, b"ancient")
        stale = _time.time() - (48 * 3600)
        os.utime(old, (stale, stale))

        maybe_persist_tool_result(
            content="v" * 60_000,
            tool_name="tool_call",
            tool_use_id="tc_prune_1",
            env=None,
            threshold=30_000,
        )

        assert not old.exists()
        assert (spill_dir / "tc_prune_1.txt").exists()

# ── recovery hint in the persisted preview ────────────────────────────

# ── MCP envelope unwrapping (#90426) ──────────────────────────────────

class TestPageableText:
    """Only the MCP handler's own envelope shape is unwrapped; every other JSON stays opaque."""

    def test_single_result_envelope_is_unwrapped(self):
        assert _pageable_text(json.dumps({"result": "line one\nline two"})) == "line one\nline two"

    def test_envelope_metadata_is_kept_after_the_text(self):
        out = _pageable_text(json.dumps({"result": "text\n", "structuredContent": {"answer": 42}}))
        text, marker, tail = out.partition("\n\n<mcp-result-metadata>\n")
        assert text == "text\n"
        assert marker
        assert json.loads(tail.split("\n</mcp-result-metadata>")[0]) == {"structuredContent": {"answer": 42}}

    @pytest.mark.parametrize(
        "content",
        [
            json.dumps({"output": "line\n", "exit_code": 0}),      # ordinary tool JSON
            json.dumps({"result": "line\n", "exit_code": 0}),      # unknown sibling key
            json.dumps([{"result": "line\n"}]),                    # not an object
            json.dumps({"result": {"blob": "line\n"}}),            # structuredContent-only style
            json.dumps({"structuredContent": {"a": 1}}),           # no model-facing text at all
            json.dumps({"result": ""}),                            # empty text
            "not json at all",
            "plain text result",
        ],
    )
    def test_unrecognized_content_is_verbatim(self, content):
        assert _pageable_text(content) == content

class TestMcpEnvelopeSpillover:
    """An oversized MCP result spills its pageable text, not one escaped JSON line (#90426)."""


    def test_envelope_spills_real_newlines(self):
        markdown = "# Research guide\n\n" + "pageable content\n" * 4_000
        envelope = json.dumps({"result": markdown}, ensure_ascii=False)
        assert len(envelope) > 30_000

        result = maybe_persist_tool_result(
            content=envelope, tool_name="mcp__archive__read_guide", tool_use_id="tc_mcp_text",
            env=None, threshold=30_000)

        assert PERSISTED_OUTPUT_TAG in result
        spill_file = get_spillover_dir() / "tc_mcp_text.txt"
        assert artifacts.read_text(spill_file) == markdown
        # read_file offset/limit is only usable if the preview is the text too.
        preview = result.split("Preview (first", 1)[1]
        assert "# Research guide" in preview
        assert "\\n" not in preview

    def test_metadata_survives_after_the_text(self):
        markdown = "body line\n" * 5_000
        envelope = json.dumps({"result": markdown, "structuredContent": {"count": 5_000}})
        maybe_persist_tool_result(
            content=envelope, tool_name="mcp__archive__structured", tool_use_id="tc_mcp_meta",
            env=None, threshold=30_000)

        spill = artifacts.read_text(get_spillover_dir() / "tc_mcp_meta.txt")
        text, _, tail = spill.partition("\n\n<mcp-result-metadata>\n")
        assert text == markdown
        assert json.loads(tail.split("\n</mcp-result-metadata>")[0]) == {"structuredContent": {"count": 5_000}}

    def test_non_envelope_json_still_verbatim(self):
        content = json.dumps({"output": "line\n" * 8_000, "exit_code": 0})
        maybe_persist_tool_result(
            content=content, tool_name="terminal", tool_use_id="tc_json_verbatim",
            env=None, threshold=30_000)
        assert artifacts.read_text(get_spillover_dir() / "tc_json_verbatim.txt") == content

    def test_structured_content_only_result_still_verbatim(self):
        content = json.dumps({"result": {"blob": "z" * 40_000}})
        maybe_persist_tool_result(
            content=content, tool_name="mcp__archive__opaque", tool_use_id="tc_mcp_opaque",
            env=None, threshold=30_000)
        assert artifacts.read_text(get_spillover_dir() / "tc_mcp_opaque.txt") == content

    def test_remote_context_preserves_pageable_text_without_upload(self):
        env = MagicMock()  # not a LocalEnvironment -> remote path
        env.execute.side_effect = [
            {"output": "", "returncode": 1},  # mounted-path probe: not readable
            {"output": "", "returncode": 0},  # cat > sandbox path
            {"output": "", "returncode": 1},  # wc -c probe: no answer -> best effort
        ]
        env.get_temp_dir.return_value = ""
        markdown = "remote line\n" * 5_000
        maybe_persist_tool_result(
            content=json.dumps({"result": markdown}), tool_name="mcp__archive__remote",
            tool_use_id="tc_mcp_remote", env=env, threshold=30_000)
        assert artifacts.read_text(get_spillover_dir() / "tc_mcp_remote.txt") == markdown
        env.execute.assert_not_called()

    def test_turn_budget_spill_unwraps_envelope(self):
        """The aggregate layer persists under __budget_enforcement__, so a tool-name gate would
        miss the very results it must fix; the shape gate still applies."""
        markdown = "budgeted line\n" * 3_000
        msgs = [{
            "role": "tool", "name": "mcp__archive__read", "tool_call_id": "tc_budget_mcp",
            "content": json.dumps({"result": markdown}),
        }]

        enforce_turn_budget(msgs, env=None, config=BudgetConfig(turn_budget=10_000))

        assert PERSISTED_OUTPUT_TAG in msgs[0]["content"]
        assert artifacts.read_text(get_spillover_dir() / "tc_budget_mcp.txt") == markdown
