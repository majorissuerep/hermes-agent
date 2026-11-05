"""Actual Linux RAM admission; macOS keeps its explicit empty-directory compatibility posture."""
from pathlib import Path

import pytest

from hermes_platform.host.facts import os_family
from tests.hermes_cli.test_managed_agents_public_cli import ram_runtime as ram_runtime  # noqa: PLC0414 -- reuse native proof fixture


@pytest.fixture(autouse=True)
def native_checker_scratch(request, _hermetic_environment, monkeypatch):
    store = request.config.getoption("--formal-checker-store")
    if store is not None:
        selected = Path(store).expanduser().resolve(strict=True)
        assert selected.is_dir(), "The explicit PM runtime must be a directory"
        monkeypatch.setenv("HERMES_RUNTIME_DIR", str(selected))
    if os_family() == "linux":
        request.getfixturevalue("ram_runtime")  # no fake proof; required lane uses --fail-on-skip
