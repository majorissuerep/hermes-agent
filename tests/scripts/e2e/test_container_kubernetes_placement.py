"""Explicit placement preserves pod isolation and rejects unsupported backends early."""
import json
import sys

import pytest

from scripts.e2e import container_lifecycle as lifecycle


@pytest.mark.parametrize("node", [None, "healthy-test-node"])
def test_requested_placement_preserves_isolation_and_records_scheduled_node(monkeypatch, node):
    manifests = []

    def command(argv, **kwargs):
        if "create" in argv:
            manifests.append(json.loads(kwargs["data"]))
        if "get" in argv:
            manifest = manifests[0]
            return json.dumps({**manifest, "metadata": {"uid": "test-pod-uid"},
                "spec": {**manifest["spec"], "nodeName": "actual-test-node"},
                "status": {"containerStatuses": [{"imageID": "resolved-pinned-image"}]}}).encode()
        return b""

    monkeypatch.setattr(lifecycle, "command", command)
    pod = lifecycle.Kubernetes("test-pod", "test-namespace", node=node)
    pod.start()
    spec = manifests[0]["spec"]
    selector = {"kubernetes.io/arch": "amd64"}
    if node:
        selector["kubernetes.io/hostname"] = node
    assert spec["nodeSelector"] == selector
    assert "nodeName" not in spec
    assert spec["automountServiceAccountToken"] is False
    assert not spec.get("volumes") and not spec.get("hostNetwork")
    container = spec["containers"][0]
    assert not container.get("volumeMounts")
    assert container["image"] == lifecycle.IMAGE
    assert container["resources"]["limits"] == {"cpu": "4", "memory": "8Gi", "ephemeral-storage": "40Gi"}
    identity = pod.identity()
    assert identity["node_name"] == "actual-test-node"
    assert identity["node_selector"] == selector
    assert identity["pod_uid"] == "test-pod-uid"


def test_docker_rejects_kubernetes_placement_before_credentials_or_evidence(monkeypatch, tmp_path, capsys):
    evidence = tmp_path / "must-not-create"
    monkeypatch.setattr(sys, "argv", ["container_lifecycle", "--backend", "docker",
        "--kubernetes-node", "healthy-test-node", "--baseline", "old", "--candidate", "new",
        "--evidence", str(evidence)])
    with pytest.raises(SystemExit) as error:
        lifecycle.main()
    assert error.value.code == 2
    assert "--kubernetes-node requires --backend kubernetes" in capsys.readouterr().err
    assert not evidence.exists()
