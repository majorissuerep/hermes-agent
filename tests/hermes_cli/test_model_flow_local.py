"""``hermes model`` → Local models runs the local runtime setup in the terminal."""

from __future__ import annotations

import pytest


def _picker_keys(config: dict) -> list[str]:
    from hermes_cli.main_provider_setup import _build_provider_picker_rows

    return [key for key, _, _ in _build_provider_picker_rows(config, "", {}, {})[0]]


def test_local_models_row_follows_the_engine_pin_and_exclusions():
    from hermes_cli.local_runtime import binaries

    assert ("llamacpp" in _picker_keys({})) == (binaries.unavailable_reason("cpu") is None)
    assert "llamacpp" not in _picker_keys({"model_catalog": {"excluded_providers": ["llama.cpp"]}})
