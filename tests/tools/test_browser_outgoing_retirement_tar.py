"""Bounded logical survivor proof; mode/mtime are deliberately outside archive semantics."""
import io
import json
import tarfile
from types import SimpleNamespace

import pytest


def _tar(path, entries):
    with tarfile.open(path, "w", format=tarfile.GNU_FORMAT) as output:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        output.addfile(root)
        for name, body, kind in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = len(body) if kind == tarfile.REGTYPE else 0
            output.addfile(member, io.BytesIO(body))


@pytest.fixture
def survivor(tmp_path):
    from hermes_security import browser_profile_archive as archive

    original = tmp_path / "authenticated-original"
    original.mkdir(mode=0o700)
    for name in ("data-a", "data-b"):
        (original / name).write_bytes(name.encode())
    binding = {"version": 1, "home": "synthetic", "identity": "a" * 64, "lease": "b" * 32}
    lease = SimpleNamespace(binding=binding, custody=SimpleNamespace(check=lambda: None))
    entries = [(archive.RUNTIME_BINDING_NAME, json.dumps(binding).encode(), tarfile.REGTYPE),
               ("data-a", b"data-a", tarfile.REGTYPE)]
    return tmp_path / "remaining.tar", original, lease, entries


def test_survivor_proof_checks_bytes_not_regenerated_file_mode_or_mtime(survivor):
    from tools.browser_outgoing_retirement import _subset

    path, original, lease, entries = survivor
    (original / "data-a").chmod(0o600)
    _tar(path, entries)
    _subset(path, original, lease)


@pytest.mark.parametrize("case", ["duplicate", "reordered", "absolute", "link", "special", "huge-metadata", "truncated", "malformed", "trailing", "entry-bound"])
def test_survivor_proof_refuses_unbounded_or_ambiguous_tar_without_writing(survivor, monkeypatch, case):
    from hermes_security import browser_profile_archive as archive
    from hermes_security import tree_archive
    from tools.browser_outgoing_retirement import _subset

    path, original, lease, entries = survivor
    before = {p.name: p.read_bytes() for p in original.iterdir()}
    additions = {
        "duplicate": [entries[-1]],
        "reordered": [("data-b", b"data-b", tarfile.REGTYPE), entries[-1]],
        "absolute": [("/outside", b"body", tarfile.REGTYPE)],
        "link": [("extra", b"", tarfile.SYMTYPE)],
        "special": [("extra", b"", tarfile.FIFOTYPE)],
        "huge-metadata": [("x" * 70000, b"body", tarfile.REGTYPE)],
    }
    entries += additions.get(case, [])
    if case == "entry-bound":
        monkeypatch.setattr(archive, "MAX_ENTRIES", 0)
    _tar(path, entries)
    data = path.read_bytes()
    if case == "truncated":
        path.write_bytes(data[:3072])  # Headers/bodies consume 2560; only one zero block remains.
    elif case == "malformed":
        path.write_bytes(b"broken-header" + data[13:])
    elif case == "trailing":
        path.write_bytes(data + b"unconfirmed" + bytes(501))
    with pytest.raises((RuntimeError, ValueError, OSError, tarfile.TarError, tree_archive.ArchiveError)):
        _subset(path, original, lease)
    assert {p.name: p.read_bytes() for p in original.iterdir()} == before
