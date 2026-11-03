"""The old bootstrap extractor preserves executable files and containment."""
import io
import os
import tarfile

import pytest

from pm.legacy_tar import extract


@pytest.mark.platforms("posix")
def test_bootstrap_archive_preserves_exec_and_relative_links_without_privileges(tmp_path):
    archive = tmp_path / "tools.tar"
    with tarfile.open(archive, "w") as stream:
        member = tarfile.TarInfo("bin/tool")
        member.mode, member.size = 0o6777, 4
        stream.addfile(member, io.BytesIO(b"tool"))
        link = tarfile.TarInfo("bin/alias")
        link.type, link.linkname = tarfile.SYMTYPE, "tool"
        stream.addfile(link)
    destination = tmp_path / "out"
    destination.mkdir()
    with tarfile.open(archive) as stream:
        extract(stream, destination)
    assert (destination / "bin/tool").read_bytes() == b"tool"
    assert (destination / "bin/tool").stat().st_mode & 0o7777 == 0o755
    assert os.readlink(destination / "bin/alias") == "tool"


@pytest.mark.parametrize("kind", ["traversal", "symlink", "hardlink", "device"])
def test_bootstrap_archive_refuses_escaping_links_and_special_files(tmp_path, kind):
    victim = tmp_path / "victim"
    victim.write_bytes(b"original")
    archive = tmp_path / "unsafe.tar"
    with tarfile.open(archive, "w") as stream:
        member = tarfile.TarInfo("../victim" if kind == "traversal" else "unsafe")
        if kind in ("symlink", "hardlink"):
            member.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
            member.linkname = "../victim"
        elif kind == "device":
            member.type = tarfile.CHRTYPE
        stream.addfile(member)
    destination = tmp_path / "out"
    destination.mkdir()
    with tarfile.open(archive) as stream, pytest.raises(ValueError):
        extract(stream, destination)
    assert victim.read_bytes() == b"original"
    assert not (destination / "unsafe").exists()
