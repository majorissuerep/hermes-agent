"""The fork's package policy agrees with its executable entrypoint."""

from pathlib import Path
import tomllib

from packaging.markers import Marker, default_environment
import pytest


def test_declared_resolver_targets_are_linux_and_macos_only():
    manifest = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    targets = manifest["tool"]["uv"]["environments"]
    for platform, supported in (("linux", True), ("darwin", True), ("win32", False)):
        environment = {**default_environment(), "sys_platform": platform, "python_version": "3.14"}
        assert any(Marker(target).evaluate(environment) for target in targets) is supported
    assert set(manifest["tool"]["hermes"]["supported-platforms"]) == {"linux", "macos", "wsl"}


def test_host_policy_rejects_native_unsupported_runtimes_with_wsl_guidance():
    from hermes_platform.support import require_supported_host

    for platform in ("linux", "darwin"):
        require_supported_host(platform)
    for platform in ("win32", "cygwin", "msys"):
        with pytest.raises(RuntimeError, match="WSL"):
            require_supported_host(platform)
