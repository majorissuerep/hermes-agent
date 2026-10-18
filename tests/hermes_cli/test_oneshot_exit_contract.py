"""``hermes -z`` exit-code contract: the outcome is judged from the turn result, not from
whether any text was printed (#111770 — an incomplete or failed run that left an explanation on
stdout used to exit 0, so scripts treated a half-done job as success)."""

from unittest import mock

from hermes_cli import oneshot


def _run(monkeypatch, tmp_path, response, result):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    usage = tmp_path / "usage.json"
    with mock.patch.object(oneshot, "_run_agent", return_value=(response, dict(result))):
        code = oneshot.run_oneshot("q", usage_file=str(usage))
    return code, usage


def test_incomplete_run_with_text_exits_nonzero_and_reports_why(monkeypatch, tmp_path):
    import json

    partial = {"final_response": "Got as far as step 2.", "completed": False, "partial": True,
               "turn_exit_reason": "iteration_limit"}
    code, usage = _run(monkeypatch, tmp_path, "Got as far as step 2.", partial)
    assert code == 2
    report = json.loads(usage.read_text(encoding="utf-8"))
    assert report["completed"] is False and report["partial"] is True
    assert report["turn_exit_reason"] == "iteration_limit"

    interrupted = {"final_response": "Stopping.", "completed": False, "interrupted": True}
    code, usage = _run(monkeypatch, tmp_path, "Stopping.", interrupted)
    assert code == 130
    assert json.loads(usage.read_text(encoding="utf-8"))["interrupted"] is True

    failed = {"final_response": "Provider returned 401.", "completed": False, "failed": True}
    code, _ = _run(monkeypatch, tmp_path, "Provider returned 401.", failed)
    assert code == 2


def test_completed_run_still_exits_zero(monkeypatch, tmp_path):
    code, _ = _run(monkeypatch, tmp_path, "Paris.", {"final_response": "Paris.", "completed": True, "failed": False})
    assert code == 0


def test_failed_empty_result_reports_reason_once_and_forces_secret_redaction(monkeypatch, tmp_path, capsys):
    from agent import redact

    monkeypatch.setattr(redact, "_REDACT_ENABLED", False)
    secret = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v2W3x4"
    result = {"failed": True, "completed": False, "error": f"FAILURE_ORACLE vendor rejected {secret}"}
    code, _ = _run(monkeypatch, tmp_path, "", result)
    output = capsys.readouterr()
    assert code == 2
    assert output.out == ""
    assert output.err.count("FAILURE_ORACLE") == 1
    assert secret not in output.err
    assert result["error"].endswith(secret), "rendering must not mutate the provider result"


def test_partial_answer_stays_stdout_and_failure_reason_stays_stderr(monkeypatch, tmp_path, capsys):
    result = {"partial": True, "completed": False, "error": "PARTIAL_FAILURE_ORACLE"}
    code, _ = _run(monkeypatch, tmp_path, "USEFUL_PARTIAL_ANSWER", result)
    output = capsys.readouterr()
    assert code == 2
    assert output.out == "USEFUL_PARTIAL_ANSWER\n"
    assert output.err.count("PARTIAL_FAILURE_ORACLE") == 1
    assert "PARTIAL_FAILURE_ORACLE" not in output.out
