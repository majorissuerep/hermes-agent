"""``/browser connect`` never offers a bypass when the protected debug profile cannot be prepared.

When the debug profile cannot be prepared safely (no proven RAM, a legacy plaintext copy awaiting
repair, a locked vault), ``launch_chrome_debug`` launches nothing and reports ``profile_error``. The
TUI must surface that reason and must NOT print a hand-run command, whose ``--user-data-dir`` would
let a browser create an unprotected profile outside the owned lifecycle.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import Mock, patch

from hermes_cli.browser_connect import ChromeDebugLaunch
from tui_runtime import server


def test_unprepared_debug_profile_reports_reason_and_offers_no_manual_command(monkeypatch):
    import urllib.request

    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)

    def no_cdp(*_args, **_kwargs):
        raise OSError("no debug browser listening")

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", no_cdp)
    refusal = ChromeDebugLaunch(profile_error="An unencrypted browser profile from an older Hermes is still "
                                              "present; run 'hermes secure-vault repair', then retry.")
    manual = Mock(return_value="/usr/bin/chromium --user-data-dir=/unprotected")
    lifecycle = types.SimpleNamespace(cleanup_all_browsers=lambda: None, _get_cdp_override=lambda: "")
    with patch.dict(sys.modules, {"tools.browser_tool_lifecycle": lifecycle}), \
            patch("hermes_cli.browser_connect.launch_chrome_debug", return_value=refusal), \
            patch("hermes_cli.browser_connect.local_port_in_use", return_value=False), \
            patch("hermes_cli.browser_connect.manual_chrome_debug_command", manual):
        resp = server.handle_request({"id": "1", "method": "browser.manage",
                                      "params": {"action": "connect", "url": "http://localhost:9222"}})

    messages = resp["result"]["messages"]
    assert resp["result"]["connected"] is False
    assert refusal.profile_error in messages
    manual.assert_not_called()
    assert not any("--user-data-dir" in message for message in messages)
