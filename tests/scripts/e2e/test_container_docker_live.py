"""Real-daemon control-plane proof for the public Docker lifecycle coordinator.

Armed by the existing real-backend lane, which fails on any skipped test.
No model credential or application state is needed for this ownership contract.
"""
import hashlib
import json
import os
import secrets
import subprocess

import pytest

from scripts.e2e.container_lifecycle import Docker, IMAGE, command


@pytest.mark.platforms("linux")
@pytest.mark.skipif(not os.environ.get("HERMES_TEST_DOCKER_IMAGE"), reason="real Docker lane not armed")
def test_real_docker_source_transport_is_isolated_and_cleanup_removes_the_container(tmp_path, monkeypatch):
    monkeypatch.setenv("E2E_LLM_API_KEY", "synthetic-parent-only-credential")
    payload = tmp_path / "source.tar"
    payload.write_bytes(secrets.token_bytes(4096))
    container = Docker("hermes-e2e-control-" + secrets.token_hex(6), None)
    try:
        container.start()
        identity = container.identity()
        actual = json.loads(command(["docker", "inspect", container.name]))[0]
        assert identity["container_id"] == actual["Id"]
        assert actual["Config"]["Image"] == IMAGE
        assert not actual["Mounts"]
        assert not actual["HostConfig"]["Privileged"]
        assert actual["HostConfig"]["NetworkMode"] != "host"
        container.put(payload)
        transferred = container.shell(["sha256sum", "/inputs/source.tar"]).decode().split()[0]
        assert transferred == hashlib.sha256(payload.read_bytes()).hexdigest()
        container.shell(["sh", "-c", 'test -z "$E2E_LLM_API_KEY"'])
    finally:
        container.close()
    absent = subprocess.run(["docker", "inspect", container.name], check=False,
                            capture_output=True, timeout=30)
    assert absent.returncode != 0
    container.close()  # Already-removed ownership is also clean, never another container.
