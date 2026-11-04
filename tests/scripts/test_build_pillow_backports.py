"""Metadata backports reject changed upstream inputs and undeclared archive members."""
import hashlib
import io
import tarfile

import pytest

from scripts.build_pillow_backports import build_archive


def _input(name="project-1/PKG-INFO", body=b"Version: 1\n"):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        entry = tarfile.TarInfo(name)
        entry.size = len(body)
        archive.addfile(entry, io.BytesIO(body))
    data = output.getvalue()
    recipe = {
        "filename": "project-1.tar.gz",
        "output": "project-1+hermes.1.tar.gz",
        "sha256": hashlib.sha256(data).hexdigest(),
        "replacements": {"PKG-INFO": [["Version: 1\n", "Version: 1+hermes.1\n", 1]]},
    }
    return data, recipe


def test_backport_is_reproducible_and_exposes_its_real_version():
    data, recipe = _input()
    output = build_archive(data, recipe)
    assert output == build_archive(data, recipe)
    with tarfile.open(fileobj=io.BytesIO(output)) as archive:
        metadata = archive.extractfile("project-1+hermes.1/PKG-INFO").read()
    assert metadata == b"Version: 1+hermes.1\n"


def test_backport_refuses_changed_bytes_or_unexpected_metadata():
    data, recipe = _input()
    with pytest.raises(ValueError, match="hash"):
        build_archive(data + b"changed", recipe)
    recipe["replacements"]["PKG-INFO"][0][0] = "unexpected"
    with pytest.raises(ValueError, match="metadata"):
        build_archive(data, recipe)


@pytest.mark.parametrize("name", ["../outside", "/absolute", "project-1/../../outside"])
def test_backport_refuses_paths_outside_the_original_package(name):
    data, recipe = _input(name)
    with pytest.raises(ValueError, match="unsafe"):
        build_archive(data, recipe)
