"""Helper contract: fixed owner jobs traverse the real management frontend seam.

The native client is a recorded seam here; actual public CLI/SSH qualification
runs separately in a disposable VM. Ordinary JSON operations keep their route.
"""
import io
import json
import sys

import pytest

from hermes_security import vault


@pytest.mark.parametrize("operation", ["browser.outgoing.repair", "browser.worker.repair"])
def test_fixed_management_request_uses_existing_native_client_only_for_operation_grant(
        tmp_path, monkeypatch, capsys, operation):
    from hermes_cli import management, session_proxy
    from hermes_security.session_authority import ENV_NAME

    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(home, "synthetic management frontend password")
    calls = []
    monkeypatch.setenv(ENV_NAME, "synthetic-operation-grant-transport-seam")
    monkeypatch.setattr(vault, "is_unlocked", lambda _home: False)
    monkeypatch.setattr(management, "execute", lambda request, _home: calls.append(("direct", request)) or {})

    def proxy(arguments, *, input_data=None):
        calls.append(("native", arguments, json.loads(input_data)))
        return 130

    monkeypatch.setattr(session_proxy, "main", proxy)
    request = {"op": operation}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    assert management.main(["apply"]) == 130
    assert calls == [("native", ["manage", "apply"], request)]
    assert capsys.readouterr().out == ""

    # Ordinary JSON management and an already-unlocked native owner retain the
    # existing direct dispatcher, rather than replaying arbitrary stdin/jobs.
    for ordinary in ({"op": "status"}, request):
        calls.clear()
        monkeypatch.setattr(vault, "is_unlocked", lambda _home: ordinary is request)
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(ordinary)))
        assert management.main(["apply"]) == 0
        assert calls == [("direct", ordinary)]
        assert json.loads(capsys.readouterr().out)["ok"] is True
