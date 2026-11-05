"""Focused verification selects real contracts and never silently broadens."""
import pytest

from scripts.ci import focused_tests


def test_profiles_are_serial_explicit_and_fail_closed(tmp_path):
    for _, files, _ in focused_tests.BATCHES:
        for name in files:
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# selected contract\n")
    selected = focused_tests.commands("focused", tmp_path)
    assert all(command[:5] == ["scripts/run_tests.sh", "-j", "1", "--file-retries", "0"]
               for command in selected)
    assert all(any(arg.endswith(".py") for arg in command) for command in selected)
    assert any("litellm" in command for command in selected)
    assert focused_tests.commands("full", tmp_path) == [
        ["scripts/run_tests.sh", "-j", "1", "--file-retries", "0"]]
    with pytest.raises(ValueError, match="unknown test profile"):
        focused_tests.commands("ful", tmp_path)
    missing = tmp_path / focused_tests.BATCHES[-1][1][0]
    missing.unlink()
    with pytest.raises(ValueError, match="missing"):
        focused_tests.commands("focused", tmp_path)


def test_cli_reports_selection_without_running_and_propagates_a_failure(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(focused_tests, "commands", lambda profile: [["contract-1"], ["contract-2"]])
    monkeypatch.setattr("sys.argv", ["focused-tests", "--plan"])
    monkeypatch.setattr(focused_tests.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    focused_tests.main()
    assert calls == []
    assert '"profile": "focused"' in capsys.readouterr().out

    def fail(command, **kwargs):
        calls.append(command)
        raise focused_tests.subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("sys.argv", ["focused-tests"])
    monkeypatch.setattr(focused_tests.subprocess, "run", fail)
    with pytest.raises(focused_tests.subprocess.CalledProcessError):
        focused_tests.main()
    assert calls == [["contract-1"]]
