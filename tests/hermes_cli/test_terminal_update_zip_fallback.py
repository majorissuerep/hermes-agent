"""The native updater retains the guarded archive repair path without hiding refusals."""
import subprocess
from types import SimpleNamespace

import pytest


@pytest.fixture
def native_update(tmp_path, monkeypatch):
    from hermes_cli import main, terminal_update, update_cmd, update_cmd_check, update_receipt

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(main, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("hermes_cli._early_recovery.git_operation_in_progress", lambda root: None)
    monkeypatch.setattr("hermes_cli.gitlock.release_dead_index_lock", lambda root: None)
    monkeypatch.setattr(update_cmd_check, "clear_git_debris", lambda root: None)
    monkeypatch.setattr(update_cmd, "_resolve_update_options", lambda *args: SimpleNamespace())
    monkeypatch.setattr(update_receipt, "begin_update_receipt", lambda: None)
    monkeypatch.setattr(update_cmd, "_run_pre_update_backup", lambda args: "snapshot-owned")
    monkeypatch.setattr(update_cmd, "_record_pre_update_backup_outcome", lambda *args: None)
    monkeypatch.setattr(update_cmd, "_record_snapshot_stage", lambda *args: None)
    request = {"snapshot_id": "snapshot-owned", "home": str(tmp_path / "home")}
    monkeypatch.setattr(update_cmd, "_source_completion_request", lambda *args: request)
    monkeypatch.setattr(update_cmd, "_prepare_git_command", lambda: (False, ["git"], False))
    monkeypatch.setattr(main, "_resolve_update_branch", lambda args: "main")
    monkeypatch.setattr(update_cmd_check, "select_apply_target", lambda *args, **kwargs:
                        ("a" * 40, "a" * 40, False, "fixture/native-source"))
    zip_calls = []
    monkeypatch.setattr(update_cmd, "_update_via_zip", lambda args, **kwargs: zip_calls.append(kwargs))
    return SimpleNamespace(module=terminal_update, update=update_cmd, request=request, zip_calls=zip_calls)


def test_eligible_git_failure_forwards_snapshot_and_exact_selected_source(native_update, monkeypatch):
    state = native_update
    failure = subprocess.CalledProcessError(128, ["git", "fetch"], stderr="native file I/O failed")
    monkeypatch.setattr(state.update, "_heal_stale_shallow_checkout", lambda *args: None)
    monkeypatch.setattr("hermes_cli.gitlock.fetch_with_partial_clone_recovery",
                        lambda *args: (_ for _ in ()).throw(failure))
    observed = []
    monkeypatch.setattr(state.update, "_should_zip_fallback_on_update_error",
                        lambda error: observed.append(error) or True)
    state.module.update_source(SimpleNamespace(branch="main"))
    assert observed == [failure]
    assert state.zip_calls == [{"had_desktop_app_before_update": False, "target_sha": "a" * 40,
                               "completion_request": state.request, "target_repository": "fixture/native-source"}]
    assert state.request["snapshot_id"] == "snapshot-owned"


@pytest.mark.parametrize("failure", [subprocess.CalledProcessError(1, ["uv", "sync"]),
                                    RuntimeError("ownership refused"), SystemExit(2)])
def test_noneligible_or_refused_git_stage_never_runs_archive_overlay(native_update, monkeypatch, failure):
    state = native_update
    monkeypatch.setattr(state.update, "_heal_stale_shallow_checkout", lambda *args: None)
    monkeypatch.setattr("hermes_cli.gitlock.fetch_with_partial_clone_recovery",
                        lambda *args: (_ for _ in ()).throw(failure))
    monkeypatch.setattr(state.update, "_should_zip_fallback_on_update_error", lambda error: False)
    with pytest.raises(type(failure)) as raised:
        state.module.update_source(SimpleNamespace(branch="main"))
    assert raised.value is failure
    assert state.zip_calls == []


def test_git_preparation_failure_still_resolves_selected_channel_before_overlay(native_update, monkeypatch):
    from hermes_cli import update_cmd_check

    state = native_update
    failure = subprocess.CalledProcessError(128, ["git", "config"])
    monkeypatch.setattr(state.update, "_prepare_git_command", lambda: (_ for _ in ()).throw(failure))
    monkeypatch.setattr(state.update, "_should_zip_fallback_on_update_error", lambda error: True)
    target_reads = []

    def resolve(args, branch, request, *, git_cmd, stop):
        target_reads.append(git_cmd)
        return "b" * 40, "b" * 40, False, "fixture/pinned-source"

    monkeypatch.setattr(update_cmd_check, "select_apply_target", resolve)
    state.module.update_source(SimpleNamespace(branch="main"))
    assert target_reads == [None]
    assert state.zip_calls[0]["target_sha"] == "b" * 40
    assert state.zip_calls[0]["target_repository"] == "fixture/pinned-source"
