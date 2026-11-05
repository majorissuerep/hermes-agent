"""The official Lean tar.zst uses PM's existing tar containment policy on Python3.14."""
from compression import zstd
import io
import tarfile

import pytest

from pm.store import extract


@pytest.mark.parametrize("escape", [False, True], ids=["retained-public-payload", "traversal-refused"])
def test_zstd_tar_uses_the_same_payload_and_containment_contract(tmp_path, escape):
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"retained outside bytes")
    payload = b"public synthetic compiler payload"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo("../sentinel" if escape else "lean/bin/lean")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    packed = tmp_path / "lean.tar.zst"
    packed.write_bytes(zstd.compress(buffer.getvalue()))
    destination = tmp_path / "output"
    if escape:
        with pytest.raises(tarfile.FilterError):
            extract(packed, destination)
    else:
        extract(packed, destination)
        assert (destination / "lean/bin/lean").read_bytes() == payload
    assert sentinel.read_bytes() == b"retained outside bytes"
