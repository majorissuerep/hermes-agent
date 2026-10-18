"""Generated image/video output must survive the gateway media sweep (#126445).

``save_b64_image`` / ``save_*_video`` write the ONLY copy of base64-returning
provider output. The hourly gateway housekeeping deletes 24h-old files from
``cache/images`` / ``cache/videos`` (transient inbound media), so generated
deliverables live in ``cache/generated/<kind>/`` instead — otherwise the
transcript and the Artifacts panel point at deleted files a day later, with no
way to recover the bytes.

Living outside the swept dirs means ``cache/generated`` needs its own
credential-files mount/sync entry: Docker/SSH/Modal backends only reach paths
that map through ``tools.credential_files``, and an unmapped path also drops
``agent_visible_image`` from the provider result.

RED on main: every test here fails — the writers target the swept caches and
``cache/generated`` is not mounted at all.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path

import pytest

from agent.image_gen_provider import save_b64_image
from agent.video_gen_provider import save_b64_video

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de00000010494441547801635c0e000000feff03000006000557bfabd400"
    "00000049454e44ae426082"
)






def test_generated_dir_is_mounted_from_the_writers_location(tmp_path, monkeypatch):
    """Out-of-sweep is only half the job: remote backends must still see the file."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from tools.credential_files import get_cache_directory_mounts

    path = save_b64_image(base64.b64encode(PNG_1PX).decode(), prefix="red_test")
    mounts = {m["container_path"]: m["host_path"] for m in get_cache_directory_mounts()}
    assert "/root/.hermes/cache/generated" in mounts, sorted(mounts)
    assert path.is_relative_to(Path(mounts["/root/.hermes/cache/generated"]).resolve())
