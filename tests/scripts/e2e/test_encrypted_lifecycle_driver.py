"""The VM driver counts actual getpass prompts and never records their answers."""
from argparse import Namespace
from pathlib import Path
import sys

import pytest

from scripts.e2e.encrypted_lifecycle import Rehearsal


@pytest.mark.platforms("linux")
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


@pytest.mark.platforms("linux")
def test_interactive_driver_detaches_only_after_independent_completion(tmp_path):
    rehearsal = Rehearsal(Namespace(
        work_dir=tmp_path, evidence=tmp_path / "evidence", keep_work=False,
        baseline="old", candidate="new", scenario="fresh", tool_cache=None,
    ), {"provider": "litellm", "model": "fixture", "api_key": "synthetic-provider-secret"})
    marker = tmp_path / "completed"
    script = ("import getpass; from pathlib import Path; "
              "getpass.getpass('Unlock master password: '); "
              f"Path({str(marker)!r}).write_text('durable result'); "
              "input('Interactive session stays open until EOF: ')")
    # EOF is a successful frontend detach, just as in GatewayChatView.
    script = "try:\n " + script + "\nexcept EOFError:\n pass\n"
    try:
        rehearsal.run("live", [sys.executable, "-c", script], prompts=1, timeout=15,
                      completion=lambda: marker.is_file() and marker.read_text() == "durable result")
        assert marker.read_text() == "durable result"
        assert rehearsal.receipt["checks"]["live"]["passphrase_prompts"] == 1
    finally:
        rehearsal.close()
