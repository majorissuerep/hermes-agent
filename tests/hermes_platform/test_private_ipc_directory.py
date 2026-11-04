"""Private socket directories keep exact permissions under group-managed temp roots."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import stat
import tempfile

import pytest


@pytest.mark.platforms("linux", "macos")
def test_private_ipc_directory_clears_inherited_group_bits_before_use(monkeypatch):
    from hermes_platform import ipc_paths
    from hermes_security.session_authority import _connection

    factory = tempfile.TemporaryDirectory
    with factory(prefix="hermes-ipc-parent-", dir="/tmp") as parent:
        os.chmod(parent, 0o2700)
        assert stat.S_IMODE(Path(parent).stat().st_mode) == 0o2700

        def allocate(*, prefix, **_options):
            return factory(prefix=prefix, dir=parent)

        with monkeypatch.context() as patch:
            patch.setattr(ipc_paths.tempfile, "TemporaryDirectory", allocate)
            with ipc_paths.private_ipc_directory(prefix="session-") as directory:
                selected = Path(directory)
                assert stat.S_IMODE(selected.stat().st_mode) == 0o700
                assert selected.stat().st_uid == os.getuid()
                path = selected / "authority.sock"
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                    listener.bind(str(path))
                    os.chmod(path, 0o600)
                    listener.listen(1)
                    with _connection({"socket": str(path)}) as client:
                        accepted, _ = listener.accept()
                        with accepted:
                            client.sendall(b"protected")
                            assert accepted.recv(32) == b"protected"
