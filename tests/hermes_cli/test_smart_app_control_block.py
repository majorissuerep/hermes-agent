"""Native dependency hints distinguish policy-blocked Python SSL from missing dependencies."""
from __future__ import annotations

from hermes_cli.main_dep_hints import smart_app_control_block_message

SAC_ERROR = ImportError(
    "DLL load failed while importing _ssl: "
    "An Application Control policy has blocked this file."
)


def test_smart_app_control_block_message_matches_the_dll_ssl_signature():
    message = smart_app_control_block_message(SAC_ERROR)
    assert message is not None
    assert "Smart App Control" in message
    assert "embedded Python runtime" in message
    assert "_ssl" in message
    assert "repair" in message  # must say the repair loop cannot fix this
    assert "https://aka.ms/smartappcontrol" in message


def test_smart_app_control_block_message_ignores_other_errors():
    assert smart_app_control_block_message(ImportError("No module named 'fastapi'")) is None
    assert smart_app_control_block_message(ImportError("cannot import name '_ssl' from 'ssl'")) is None
