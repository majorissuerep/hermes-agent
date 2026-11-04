"""Native dispatch uses exact local source identity over actual session transports."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import sys

import pytest

from hermes_security import vault
from tests.waiting import wait_until

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.platforms("linux", "macos")


@pytest.fixture
def owner(tmp_path, monkeypatch):
    home = tmp_path / "owner"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(home, "synthetic source grant credential")
    unlocked = vault.unlock(home, "synthetic source grant credential")
    unlocked.write_bytes("payload", b"synthetic owned data", purpose="probe")
    yield home
    vault.clear_vault_cache()


@pytest.mark.parametrize("pipe", [True, False], ids=["fifo", "unix-socket"])
def test_native_start_over_real_transport_adopts_exact_fd_and_returns_output(owner, monkeypatch, pipe):
    from hermes_cli import _launchers
    from hermes_security.session_authority import ENV_NAME, operation_request, start_session_authority

    authority = start_session_authority(owner, "native-source-owner", pipe=pipe)
    code = (f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
            "from hermes_security.handoff import adopt_inherited_key; "
            "from hermes_security.vault import get_vault; "
            "assert adopt_inherited_key(); "
            f"assert get_vault({str(owner)!r}).read_bytes('payload', purpose='probe') == b'synthetic owned data'; "
            "print('owned-native-child')")
    monkeypatch.setattr(_launchers, "runtime_command", lambda *args: [sys.executable, "-I", "-c", code])
    raw = authority.environment()[ENV_NAME]
    output = []
    response = None

    def complete():
        nonlocal response
        response = operation_request(owner, raw, {"op": "native.poll", "job": job})
        output.extend(base64.b64decode(chunk["data"], validate=True) for chunk in response.get("chunks", [])
                      if chunk["stream"] == "stdout")
        return response.get("done")

    try:
        started = operation_request(owner, raw, {"op": "native.start", "arguments": ["status"]})
        assert started.get("ok") is True
        job = started["job"]
        assert wait_until(complete, timeout=20)
        assert response["returncode"] == 0
        assert b"".join(output).strip() == b"owned-native-child"
    finally:
        authority.close()


@pytest.mark.parametrize("change", ["token", "home", "session_id", "scope", "version", "pipe", "socket", "closed"])
def test_local_native_source_requires_full_live_issuing_identity(owner, change):
    from hermes_security.session_authority import (
        ENV_NAME, SessionAuthorityError, start_session_authority, validate_local_source_grant,
    )

    authority = start_session_authority(owner, "exact-source-owner", pipe=True)
    grant = json.loads(authority.environment()[ENV_NAME])
    try:
        if change == "closed":
            authority.close()
        else:
            grant[change] = 1 if change == "version" else "synthetic-mismatched-identity"
        with pytest.raises(SessionAuthorityError):
            validate_local_source_grant(owner, json.dumps(grant))
    finally:
        authority.close()
