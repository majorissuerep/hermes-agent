"""A recognized binary format is not reinterpreted by the UTF-16 heuristic."""

import pytest

from tools.environments.local import LocalEnvironment
from tools.file_operations import ShellFileOperations


@pytest.mark.platforms("linux", "macos")
@pytest.mark.parametrize(
    "payload,kind",
    [
        (b"HRMVAULT\x00\x01" + b"\x01\x02" * 40 + b"\x00\x02", "encrypted Hermes state"),
        (b"\x7fELF" + b"a\x00" * 40, "ELF executable"),
    ],
)
def test_binary_magic_beats_utf16_zero_parity(tmp_path, payload, kind):
    path = tmp_path / "misnamed.txt"
    path.write_bytes(payload)
    env = LocalEnvironment(cwd=str(tmp_path))
    try:
        result = ShellFileOperations(env, cwd=str(tmp_path)).read_file(str(path))
        assert result.is_binary, result
        assert kind in result.error
        assert not result.content
    finally:
        env.cleanup()
