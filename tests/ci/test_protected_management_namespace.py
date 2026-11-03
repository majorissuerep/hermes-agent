"""CI protection must refuse to change mounts in its caller's namespace."""
import os

import pytest


@pytest.mark.platforms("linux")
def test_management_qualification_refuses_the_callers_mount_namespace(tmp_path):
    from scripts.ci.protected_management_checks import inside_namespace

    # A nonempty mountpoint also makes an accidental bypass fail before mount.
    marker = tmp_path / "synthetic-canary"
    marker.write_text("preserve this file", encoding="utf-8")
    descriptor = os.open("/proc/self/ns/mnt", os.O_RDONLY)
    try:
        with pytest.raises(RuntimeError, match="separate mount namespace"):
            inside_namespace(tmp_path, descriptor)
    finally:
        os.close(descriptor)
    assert marker.read_text(encoding="utf-8") == "preserve this file"
