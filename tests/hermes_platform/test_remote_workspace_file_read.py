"""A protected target file reader never streams a link or a changed allocated file."""
import pytest


@pytest.mark.platforms("linux")
@pytest.mark.parametrize("kind", ["regular", "symlink", "hardlink"])
def test_remote_capture_reader_pins_only_the_allocated_single_link_file(tmp_path, kind):
    import os
    import subprocess
    from hermes_platform.remote_workspace import regular_file_read_argv

    source, selected = tmp_path / "source", tmp_path / "capture"
    payload = b"synthetic recording binary\x00\xff"
    source.write_bytes(payload)
    if kind == "regular":
        source.rename(selected)
    elif kind == "symlink":
        selected.symlink_to(source)
    else:
        os.link(source, selected)
    result = subprocess.run(regular_file_read_argv(str(selected), max_bytes=100),
                            capture_output=True, timeout=10, check=False)
    if kind == "regular":
        assert result.returncode == 0 and result.stdout == payload
    else:
        assert result.returncode != 0 and result.stdout == b""
        assert source.read_bytes() == payload
    assert sorted(path.name for path in tmp_path.iterdir()) == (["capture"] if kind == "regular" else ["capture", "source"])
