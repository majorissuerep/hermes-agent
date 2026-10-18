"""The isolated driver counts actual getpass prompts and never records their answers."""
from argparse import Namespace
import io
import json
import os
from pathlib import Path
import sys
import shlex
import subprocess

import pytest

from scripts.e2e.encrypted_lifecycle import Rehearsal


@pytest.mark.parametrize("credentials_stdin", [False, True])
def test_unreachable_gateway_stops_before_installation_or_work_creation(tmp_path, monkeypatch, credentials_stdin):
    import socket

    from scripts.e2e import encrypted_lifecycle as driver

    source = Path(__file__).resolve().parents[3]
    work = tmp_path / "disposable-work"
    work.mkdir()
    evidence = tmp_path / "evidence"
    with socket.socket() as unavailable:
        # Keep the port reserved but not listening: no external DNS, service or race.
        unavailable.bind(("127.0.0.1", 0))
        monkeypatch.setenv("E2E_LLM_PROVIDER", "litellm")
        monkeypatch.setenv("E2E_LLM_MODEL", "fixture")
        monkeypatch.setenv("E2E_LLM_API_KEY", "synthetic-provider-secret")
        monkeypatch.setenv("E2E_LLM_BASE_URL", f"http://127.0.0.1:{unavailable.getsockname()[1]}/v1")
        argv = ["encrypted_lifecycle.py", "--scenario", "fresh",
            "--source", str(source), "--baseline", "HEAD", "--candidate", "HEAD",
            "--work-dir", str(work), "--evidence", str(evidence)]
        if credentials_stdin:
            argv.append("--credentials-stdin")
            monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
                "provider": "litellm", "model": "fixture", "api_key": "synthetic-provider-secret",
                "base_url": f"http://127.0.0.1:{unavailable.getsockname()[1]}/v1"})))
        monkeypatch.setattr(sys, "argv", argv)

        def must_not_install(_rehearsal):
            pytest.fail("installation started before provider transport preflight")

        monkeypatch.setattr(Rehearsal, "execute", must_not_install)
        with pytest.raises(RuntimeError, match="gateway is unreachable"):
            driver.main()
    assert list(work.iterdir()) == []
    assert not evidence.exists()
    assert "E2E_LLM_API_KEY" not in os.environ


def test_failed_runtime_cleanup_cannot_publish_green_or_delete_its_work(tmp_path, monkeypatch):
    rehearsal = Rehearsal(Namespace(
        work_dir=tmp_path, evidence=tmp_path / "evidence", keep_work=False,
        baseline="old", candidate="new", scenario="update", tool_cache=None,
    ), {"provider": "litellm", "model": "fixture", "api_key": "synthetic-provider-secret"})
    rehearsal.python = Path(sys.executable)
    rehearsal.home.mkdir()
    (rehearsal.home / ".hermes-vault").touch()
    rehearsal.receipt["status"] = "passed"

    def failed_stop(action):
        assert action == "verify-runtime-closed"
        raise RuntimeError("local runtime survived; " + rehearsal.password)

    monkeypatch.setattr(rehearsal, "probe", failed_stop)
    with pytest.raises(RuntimeError, match="work retained"):
        rehearsal.close()
    receipt = (rehearsal.evidence / "receipt.json").read_text()
    assert json.loads(receipt)["status"] == "failed"
    assert rehearsal.password not in receipt
    assert rehearsal.root.is_dir()


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("actual_prompts", [1, 2])
@pytest.mark.parametrize("prompt", ["Master password: ", "Unlock master password: "])
def test_actual_pty_prompt_budget_is_enforced_and_credentials_are_redacted(tmp_path, actual_prompts, prompt):
    rehearsal = Rehearsal(Namespace(
        work_dir=tmp_path, evidence=tmp_path / "evidence", keep_work=False,
        baseline="old", candidate="new", scenario="update", tool_cache=None,
    ), {"provider": "litellm", "model": "fixture", "api_key": "synthetic-provider-secret"})
    script = ("import getpass; "
              f"answers=[getpass.getpass({prompt!r}) for _ in range({actual_prompts})]; "
              "print(answers[0])")
    try:
        if actual_prompts == 1:
            rehearsal.run("update", [sys.executable, "-c", script], prompts=1, timeout=10)
        else:
            with pytest.raises(RuntimeError, match="extra passphrase prompt"):
                rehearsal.run("update", [sys.executable, "-c", script], prompts=1, timeout=10)
        log = (tmp_path / "evidence" / "update.log").read_text()
        assert rehearsal.password not in log
        assert "synthetic-provider-secret" not in log
        assert prompt.strip() in log
    finally:
        rehearsal.close()


@pytest.mark.platforms("posix")
def test_git_inspection_never_enters_an_interactive_pager(tmp_path):
    rehearsal = Rehearsal(Namespace(
        work_dir=tmp_path, evidence=tmp_path / "evidence", keep_work=False,
        baseline="old", candidate="new", scenario="broken-files", tool_cache=None,
    ), {"provider": "litellm", "model": "fixture", "api_key": "synthetic-provider-secret"})
    marker = tmp_path / "pager-entered"
    pager = shlex.join([sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"])
    try:
        expected = "retain local work\n" * 100
        (rehearsal.workspace / "note").write_text(expected)
        for args in (["init"], ["add", "note"],
                     ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture"]):
            subprocess.run(["git", *args], cwd=rehearsal.workspace, env=rehearsal.env,
                           capture_output=True, check=True)
        actual = rehearsal.run("inspect", ["git", "-c", "core.pager=" + pager, "show", "HEAD:note"], timeout=10)
        assert actual.replace("\r", "").strip() == expected.strip()
        assert not marker.exists()
    finally:
        rehearsal.close()


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("exit_code", [0, 7])
def test_headless_driver_requires_normal_exit_even_after_durable_output(tmp_path, exit_code):
    rehearsal = Rehearsal(Namespace(
        work_dir=tmp_path, evidence=tmp_path / "evidence", keep_work=False,
        baseline="old", candidate="new", scenario="fresh", tool_cache=None,
    ), {"provider": "litellm", "model": "fixture", "api_key": "synthetic-provider-secret"})
    marker = tmp_path / "completed"
    script = ("import getpass; from pathlib import Path; "
              "getpass.getpass('Unlock master password: '); "
              f"Path({str(marker)!r}).write_text('durable result'); "
              f"raise SystemExit({exit_code})")
    try:
        if exit_code:
            with pytest.raises(RuntimeError, match="exit=7"):
                rehearsal.run("live", [sys.executable, "-c", script], prompts=1, timeout=15)
        else:
            rehearsal.run("live", [sys.executable, "-c", script], prompts=1, timeout=15)
        assert marker.read_text() == "durable result"
        assert rehearsal.receipt["checks"]["live"]["passphrase_prompts"] == 1
    finally:
        rehearsal.close()
