"""Native container log directories are writable by the runtime user."""
from __future__ import annotations

from tests.docker.conftest import (
    docker_exec_sh,
    start_container,
)


def test_logs_seeded_and_hermes_owned(built_image: str, container_name: str) -> None:
    start_container(built_image, container_name)
    result = docker_exec_sh(container_name,
                            'test -d /opt/data/logs && test -w /opt/data/logs && stat -c "%U" /opt/data/logs',
                            timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "hermes"
