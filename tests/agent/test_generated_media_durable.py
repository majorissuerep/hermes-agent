"""An expiring provider's only-copy output remains encrypted and locally recoverable."""
from __future__ import annotations

import base64
import io
import os
import time
import tarfile

from PIL import Image

from agent.image_gen_provider import save_b64_image
from hermes_security import artifacts, vault
from hermes_security.recovery_export import export_home


def test_generated_output_is_durable_without_automatic_remote_plaintext_sync(tmp_path, monkeypatch):
    from tools.credential_files import get_cache_directory_mounts, iter_cache_files

    home = tmp_path / "owner"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(home, "synthetic generated output password")
    vault.unlock(home, "synthetic generated output password")
    try:
        source = io.BytesIO()
        Image.new("RGB", (2, 2), "blue").save(source, format="PNG")
        original = source.getvalue()
        path = save_b64_image(base64.b64encode(original).decode(), prefix="durable_test")
        old = time.time() - 25 * 3600
        os.utime(path, (old, old))
        assert artifacts.read_bytes(path) == original
        assert all(not path.is_relative_to(m["host_path"]) for m in get_cache_directory_mounts())
        assert str(path) not in {m["host_path"] for m in iter_cache_files()}
        destination = tmp_path / "explicit-offline-export.tar"
        export_home(home, destination)
        with tarfile.open(destination) as archive:
            assert archive.extractfile(path.relative_to(home).as_posix()).read() == original
        assert artifacts.read_bytes(path) == original
    finally:
        vault.clear_vault_cache()
