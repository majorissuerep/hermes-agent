"""Explicit terminal selections remain diagnostic targets on the actual host."""

import pytest

from hermes_cli import doctor_tools


@pytest.mark.parametrize("backend", ["ssh", "daytona", "vercel_sandbox", "plugin-fixture"])
def test_explicit_terminal_backend_is_not_replaced_by_local(monkeypatch, backend):
    seen = []
    monkeypatch.setenv("TERMINAL_ENV", backend)
    monkeypatch.setattr(doctor_tools, "_check_docker_backend", lambda selected, container, issues: seen.append(selected))
    monkeypatch.setattr(doctor_tools, "_BACKEND_CHECKS", {
        name: lambda issues, name=name: seen.append(name)
        for name in ("ssh", "daytona", "vercel_sandbox")
    })
    monkeypatch.setattr(doctor_tools, "_check_plugin_backend", lambda selected, issues: seen.append(selected))

    doctor_tools._check_terminal_backend(False)

    assert seen == [backend, backend]


def test_local_terminal_selection_does_not_probe_remote_backend(monkeypatch):
    seen = []
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setattr(doctor_tools, "_check_docker_backend", lambda selected, container, issues: seen.append(selected))
    monkeypatch.setattr(doctor_tools, "_check_plugin_backend", lambda *args: pytest.fail("local requested remote diagnostics"))

    doctor_tools._check_terminal_backend(False)

    assert seen == ["local"]
