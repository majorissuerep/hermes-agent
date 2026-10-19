"""Local inference remains selectable through an ordinary custom endpoint."""

from __future__ import annotations


def test_local_custom_endpoint_remains_selectable_without_builtin_engine_wizard():
    from hermes_cli.main_provider_setup import _build_provider_picker_rows

    rows, selected = _build_provider_picker_rows({}, "custom:local", {}, {
        "custom:local": {"name": "Local inference", "base_url": "http://127.0.0.1:8080/v1", "model": "local-model"}})
    keys = [key for key, _, _ in rows]
    assert "llamacpp" not in keys
    assert "custom" in keys
    assert rows[selected][0] == "custom:local"
    assert "127.0.0.1:8080/v1" in rows[selected][1]
    assert "local-model" in rows[selected][1]
