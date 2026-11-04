"""A selected default home must not reap another default home's retained browser state.

Docker is a structured wire stand-in. No Docker daemon or application runs on the host.
"""
import datetime
import hashlib
from types import SimpleNamespace

import pytest

from tools.environments import docker


def _home_id(home):
    return hashlib.sha256(str(home.resolve()).encode()).hexdigest()[:32]


@pytest.mark.parametrize("selected", ["A", "B"])
def test_orphan_sweep_preserves_pending_foreign_legacy_and_shared_owners(tmp_path, monkeypatch, selected):
    homes = {name: tmp_path / name for name in ("A", "B")}
    for home in homes.values():
        home.mkdir(mode=0o700)
    pending = homes["A"] / "state/browser-outgoing" / ("a" * 64 + ".json")
    pending.parent.mkdir(parents=True, mode=0o700)
    (homes["A"] / "state").chmod(0o700)
    pending.write_bytes(b"damaged encrypted recovery reference: preserve without decrypting")
    pending.chmod(0o600)
    original = pending.read_bytes()
    monkeypatch.setenv("HERMES_HOME", str(homes[selected]))
    rows = {
        "pending-a": {"hermes-profile": "default", "hermes-home": _home_id(homes["A"]), "hermes-ownership": "exclusive"},
        "ordinary-b": {"hermes-profile": "default", "hermes-home": _home_id(homes["B"]), "hermes-ownership": "exclusive"},
        "legacy": {"hermes-profile": "default"},
        "shared-b": {"hermes-profile": "default", "hermes-home": _home_id(homes["B"]), "hermes-ownership": "shared"},
    }
    removed = []

    def query(argv, **_kwargs):
        if argv[1] == "ps":
            filters = [argv[index + 1][6:].split("=", 1) for index, arg in enumerate(argv[:-1])
                       if arg == "--filter" and argv[index + 1].startswith("label=")
                       and argv[index + 1] != "label=hermes-agent=1"]
            matches = [name for name, labels in rows.items() if all(labels.get(key) == value for key, value in filters)]
            return SimpleNamespace(returncode=0, stdout="\n".join(matches), stderr="")
        assert argv[1] == "rm" and "-f" not in argv
        removed.append(argv[-1])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(docker, "_docker_query", query)
    monkeypatch.setattr(docker, "_container_finished_at", lambda *_args: datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1))
    count = docker.reap_orphan_containers(profile_filter="default", docker_exe="unit-docker")
    assert removed == ([] if selected == "A" else ["ordinary-b"])
    assert count == len(removed)
    assert pending.read_bytes() == original
