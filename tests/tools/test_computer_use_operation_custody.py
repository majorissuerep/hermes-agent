"""Delayed desktop captures retain their original session and unlock authority.

Real vault/source checks, synthetic capture backend only; this is not native GUI proof.
"""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.tools.test_computer_use_private_media import SHOT, _capture, _inline_image

pytest_plugins = ["tests.tools.test_computer_use_private_media"]


@pytest.mark.parametrize("change", ["session-close", "vault-reunlock"])
def test_delayed_capture_cannot_borrow_a_sibling_or_fresh_unlock(home, native_route, monkeypatch, change):
    from agent.session_context import scoped_current_session_id
    from hermes_security import artifacts, vault
    from hermes_security.session_authority import start_session_authority

    first_id, sibling_id = f"capture-A-{change}", f"capture-B-{change}"
    first = start_session_authority(home, first_id)
    sibling = start_session_authority(home, sibling_id)

    def delayed_capture(**_kwargs):
        if change == "session-close":
            first.close()
        else:
            vault.lock_now(home)
            vault.unlock(home, "synthetic computer_use custody password")
        return _capture()

    @contextmanager
    def backend(_session_id):
        yield SimpleNamespace(capture=delayed_capture)

    monkeypatch.setattr(native_route, "_backend_for_call", backend)
    try:
        with scoped_current_session_id(first_id):
            result = native_route.handle_computer_use({"action": "capture"}, session_id=first_id)
        assert isinstance(result, dict), result
        assert result["_multimodal"] is True and _inline_image(result) == SHOT
        assert "screenshot_path" not in result["meta"]
        assert "elements_file" not in result["meta"]
        assert result["meta"]["screenshot_unavailable"]
        assert result["meta"]["elements_file_unavailable"]
        assert not [path for path in (home / "artifacts").rglob("*") if path.is_file()]
        assert vault.is_unlocked(home)

        if change == "vault-reunlock":
            sibling.close()
            sibling = start_session_authority(home, sibling_id)

        @contextmanager
        def fresh_backend(_session_id):
            yield SimpleNamespace(capture=lambda **_kwargs: _capture())

        monkeypatch.setattr(native_route, "_backend_for_call", fresh_backend)
        with scoped_current_session_id(sibling_id):
            fresh = native_route.handle_computer_use({"action": "capture"}, session_id=sibling_id)
        retained = Path(fresh["meta"]["screenshot_path"])
        assert artifacts.read_bytes(retained, home=home) == SHOT
        assert Path(fresh["meta"]["elements_file"]).is_file()
    finally:
        first.close()
        sibling.close()
