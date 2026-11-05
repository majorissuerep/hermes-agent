"""Release packaging refuses incomplete or nonportable terminal products."""
import hashlib
import json
import tarfile

import pytest

from scripts.bundles.release import package_payload

COMMIT = "1" * 40


@pytest.fixture
def payload(tmp_path):
    root = tmp_path / "payload"
    for directory in ("hermes-agent/hermes_cli/tui_dist", "venv/site", "tools/python",
                      "pm-runtime", "uv-cache", "bin"):
        (root / directory).mkdir(parents=True)
    for name in ("tools/python/python", "bin/hermes", "pm-runtime/pm-runtime.json",
                 "tools/facts.json", "hermes-agent/hermes_cli/tui_dist/entry.js"):
        (root / name).write_text("prepared product")
    (root / "hermes-agent/hermes_cli/tui_dist/package.json").write_text('{"type":"module"}')
    (root / "hermes-agent/pyproject.toml").write_text('[project]\nversion = "1.1.0"\n')
    manifest = {"schema": 1, "ref": COMMIT, "target": "linux-x64",
                "repo": "hermes-agent", "venv": "venv", "store": "tools",
                "runtime": {"repoDir": "hermes-agent", "toolsDir": "tools",
                            "storePython": "tools/python/python", "sitePackages": "venv/site",
                            "commands": {"hermes": "bin/hermes"}}}
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root


def test_complete_product_keeps_source_and_portable_layout(payload, tmp_path):
    receipt = package_payload(payload, tmp_path / "assets", COMMIT, "linux-x64")
    data = json.loads(receipt.read_text())
    archive = receipt.parent / data["archive"]
    with archive.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == data["sha256"]
    assert data["source"] == COMMIT
    with tarfile.open(archive) as tar:
        assert tar.extractfile("hermes/hermes-agent/hermes_cli/tui_dist/entry.js").read() == b"prepared product"
        assert json.load(tar.extractfile("hermes/manifest.json"))["ref"] == COMMIT
    with pytest.raises(FileExistsError):
        package_payload(payload, receipt.parent, COMMIT, "linux-x64")


@pytest.mark.parametrize("defect", ["missing_tui", "wrong_source", "escaping_link"])
def test_unsafe_or_dependency_only_output_cannot_be_published(payload, tmp_path, defect):
    if defect == "missing_tui":
        (payload / "hermes-agent/hermes_cli/tui_dist/entry.js").unlink()
    elif defect == "wrong_source":
        data = json.loads((payload / "manifest.json").read_text())
        data["ref"] = "2" * 40
        (payload / "manifest.json").write_text(json.dumps(data))
    else:
        (payload / "outside").symlink_to(tmp_path / "external")
        (tmp_path / "external").write_text("outside payload")
    out = tmp_path / "assets"
    with pytest.raises((ValueError, FileNotFoundError)):
        package_payload(payload, out, COMMIT, "linux-x64")
    assert not out.exists()
