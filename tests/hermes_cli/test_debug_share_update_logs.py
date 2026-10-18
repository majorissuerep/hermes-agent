"""Native debug reports preserve terminal update and outgoing MCP diagnostics."""

import pytest

# Lines the writers really produce (main.py _UpdateOutputStream mirror and
# scripts/desktop-update/windows.ps1 Write-HandoffLog / posix.sh log()).
UPDATE_LOG_BODY = (
    "=== hermes update started 2026-09-02T03:21:09 ===\n"
    "→ Fetching updates...\n"
    "→ Building TUI...\n"
    "✗ TUI build failed\n"
)
MCP_LOG_BODY = (
    "2026-09-02T03:21:08 MCP stdio exit code: 1\n"
    "2026-09-02T03:21:08 stdio initialization failed\n"
)


@pytest.fixture
def home_with_update_logs(tmp_path, monkeypatch):
    """Isolated HERMES_HOME whose logs directory holds the update pair."""
    home = tmp_path / "hermes_test_home"
    logs = home / "logs"
    logs.mkdir(parents=True)
    (logs / "agent.log").write_text(
        "2026-09-02 03:21:00 INFO agent.conversation_loop: API call #1\n"
    )
    (logs / "update.log").write_text(UPDATE_LOG_BODY)
    (logs / "mcp-stderr.log").write_text(MCP_LOG_BODY)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


@pytest.fixture
def home_without_update_logs(tmp_path, monkeypatch):
    """Isolated HERMES_HOME with only the classic five logs present."""
    home = tmp_path / "hermes_test_home"
    logs = home / "logs"
    logs.mkdir(parents=True)
    for name in ("agent.log", "errors.log"):
        (logs / name).write_text("2026-09-02 03:21:00 INFO x: one line\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


class TestCaptureDefaultLogSnapshots:
    """The snapshot collector must include the update/hand-off pair."""

    def test_snapshots_include_update_and_mcp_keys(self, home_with_update_logs):
        from hermes_cli.debug import _capture_default_log_snapshots

        snaps = _capture_default_log_snapshots(100)

        assert "update" in snaps
        assert "mcp" in snaps
        assert "TUI build failed" in snaps["update"].tail_text
        assert "stdio" in snaps["mcp"].tail_text

    def test_snapshots_read_the_root_home_update_logs_under_a_profile(self, tmp_path, monkeypatch):
        """``hermes update`` and the hand-off scripts write the ROOT home's logs, whatever profile."""
        from hermes_cli.debug import _capture_default_log_snapshots

        root = tmp_path / "root"
        profile = root / "profiles" / "coder"
        (root / "logs").mkdir(parents=True)
        (profile / "logs").mkdir(parents=True)
        (root / "logs" / "update.log").write_text(UPDATE_LOG_BODY)
        (profile / "logs" / "mcp-stderr.log").write_text(MCP_LOG_BODY)
        monkeypatch.setenv("HERMES_HOME", str(profile))

        snaps = _capture_default_log_snapshots(100)

        assert "TUI build failed" in snaps["update"].tail_text
        assert "stdio" in snaps["mcp"].tail_text

    def test_missing_files_report_absence_without_raising(
        self, home_without_update_logs
    ):
        """A fresh install has no update logs — capture degrades, not crashes."""
        from hermes_cli.debug import _capture_default_log_snapshots

        snaps = _capture_default_log_snapshots(100)

        # Keys exist; the snapshots describe the missing files honestly.
        assert "update" in snaps
        assert "mcp" in snaps
        assert "not found" in snaps["update"].tail_text
        assert "not found" in snaps["mcp"].tail_text


class TestCollectDebugReport:
    """The summary report (what users paste) shows the update tails."""

    def test_report_contains_update_and_mcp_sections(self, home_with_update_logs):
        from hermes_cli.debug import collect_debug_report

        report = collect_debug_report(log_lines=50, dump_text="dump")

        assert "--- update.log" in report
        assert "TUI build failed" in report
        assert "--- mcp-stderr.log" in report
        assert "stdio" in report

    def test_report_omits_update_sections_when_logs_absent(
        self, home_without_update_logs
    ):
        from hermes_cli.debug import collect_debug_report

        report = collect_debug_report(log_lines=50, dump_text="dump")

        # Honest absence: the section header appears with a missing-note
        # (snapshot present), never a KeyError. The classic five remain.
        assert "--- agent.log" in report
        assert "--- update.log" in report
        assert "--- mcp-stderr.log" in report

    def test_report_tolerates_a_hand_built_snapshots_dict(self, home_with_update_logs):
        """Gateway /debug and older callers pass dicts without the new keys.

        They must keep working (no KeyError) — degraded to the old
        five-section report rather than crashing.
        """
        from hermes_cli.debug import LogSnapshot, collect_debug_report

        legacy_keys = ("agent", "errors")
        legacy = {k: LogSnapshot(path=None, tail_text="", full_text="") for k in legacy_keys}

        report = collect_debug_report(
            log_lines=50, dump_text="dump", log_snapshots=legacy
        )

        assert "--- agent.log" in report
        assert "--- update.log" not in report


class TestCollectShareBundle:
    """The upload bundle (paste.rs + --nous) carries the full update logs."""

    def test_bundle_includes_update_logs_when_present(self, home_with_update_logs):
        from hermes_cli.debug import collect_share_bundle

        bundle = collect_share_bundle(log_lines=50, redact=False)

        assert "update.log" in bundle
        assert "TUI build failed" in bundle["update.log"]
        assert "--- full update.log ---" in bundle["update.log"]
        assert "mcp-stderr.log" in bundle
        assert "stdio" in bundle["mcp-stderr.log"]

    def test_bundle_omits_update_keys_when_files_absent(
        self, home_without_update_logs
    ):
        from hermes_cli.debug import collect_share_bundle

        bundle = collect_share_bundle(log_lines=50, redact=False)

        assert "update.log" not in bundle
        assert "mcp-stderr.log" not in bundle
        # The classic five keep uploading.
        assert "agent.log" in bundle


class TestFullLogUploadLabels:
    def test_build_uploads_native_optional_logs(self, monkeypatch):
        from hermes_cli import debug
        bundle = {"report": "summary", "agent.log": "agent", "mcp-stderr.log": "mcp", "update.log": "update"}
        monkeypatch.setattr(debug, "collect_share_bundle", lambda **kwargs: bundle)
        monkeypatch.setattr(debug, "_schedule_auto_delete", lambda *args: None)
        uploads = []
        monkeypatch.setattr(debug, "upload_to_pastebin",
                            lambda content, **kwargs: uploads.append(content) or "https://example.invalid/test")
        result = debug.build_debug_share()
        assert set(result.urls) == {"Report", "agent.log", "mcp-stderr.log", "update.log"}
        assert uploads == ["summary", "agent", "mcp", "update"]

    def test_local_prints_same_optional_logs_without_upload(self, monkeypatch, capsys):
        from types import SimpleNamespace
        from hermes_cli import debug
        monkeypatch.setattr(debug, "collect_share_bundle",
                            lambda **kwargs: {"report": "summary", "mcp-stderr.log": "mcp-body", "update.log": "update-body"})
        monkeypatch.setattr(debug, "upload_to_pastebin", lambda *args, **kwargs: pytest.fail("local report uploaded"))
        debug.run_debug_share(SimpleNamespace(local=True, lines=20, expire=1, no_redact=False))
        output = capsys.readouterr().out
        assert "FULL mcp-stderr.log" in output and "mcp-body" in output
        assert "FULL update.log" in output and "update-body" in output


class TestLogsRegistry:
    """LOG_FILES gains readable keys for files `hermes logs list` already shows."""

    def test_update_and_mcp_keys_resolve_real_filenames(self):
        from hermes_cli.logs import LOG_FILES

        assert LOG_FILES.get("update") == "update.log"
        assert LOG_FILES.get("mcp") == "mcp-stderr.log"

    def test_tail_log_reads_update_log_from_hermes_home(self, home_with_update_logs):
        """End-to-end: `hermes logs update` resolves and prints the file."""
        from hermes_cli import logs as logs_mod

        logs_mod.tail_log("update", num_lines=10)

    def test_tail_log_names_the_new_logs_when_unknown_requested(
        self, home_with_update_logs, capsys
    ):
        from hermes_cli import logs as logs_mod

        with pytest.raises(SystemExit) as excinfo:
            logs_mod.tail_log("no-such-log")

        assert excinfo.value.code == 1
        # The available-log hint teaches the new keys.
        hint = capsys.readouterr().out
        assert "update" in hint
        assert "mcp" in hint
