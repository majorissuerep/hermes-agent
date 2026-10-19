"""Captured Git failure reasons survive the terminal update boundary without credentials."""
import subprocess

import pytest

from hermes_cli.terminal_update import format_update_failure


@pytest.mark.parametrize("reason", ["Permission denied", "Could not resolve host: forge.invalid", "HTTP 429 rate limit"])
def test_update_failure_preserves_captured_reason_and_redacts_url_credentials(reason):
    error = subprocess.CalledProcessError(128, ["git", "fetch"],
        output=b"", stderr=f"fatal: {reason}\nhttps://private-user:private-password@forge.invalid/repo.git".encode())
    rendered = format_update_failure(error)
    assert reason in rendered
    assert "128" in rendered and "git" in rendered
    assert "private-password" not in rendered and "private-user" not in rendered


def test_non_subprocess_failure_retains_original_reason():
    assert format_update_failure(OSError("read-only filesystem")) == "✗ Update failed: read-only filesystem"
