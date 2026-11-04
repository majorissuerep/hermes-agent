"""Owner binding for desktop/clipboard image uploads (#69575).

Attach RPCs (``image.attach_bytes``, ``clipboard.paste``, ``pdf.attach``) run
before ``prompt.submit`` installs the session's profile HERMES_HOME override, so
the upload is sealed for the session's *stored* ``profile_home`` — the same owner
whose custody vision and resume later use to read it back. Otherwise, in a
multi-profile process, the upload would belong to the launch profile and the
session's own turns could never decrypt it.
"""

from unittest.mock import patch

from tui_runtime.server import _session_owner_home


def test_profile_home_session_is_owned_by_its_profile(tmp_path):
    profile_home = tmp_path / ".hermes" / "profiles" / "coder"
    assert _session_owner_home({"profile_home": str(profile_home)}) == profile_home


def test_launch_home_owns_a_session_without_profile(tmp_path):
    launch_home = tmp_path / ".hermes"
    with patch("tui_runtime.server._hermes_home", launch_home):
        assert _session_owner_home({}) == launch_home
        # An empty-string ``profile_home`` is treated as absent, not as the filesystem root.
        assert _session_owner_home({"profile_home": ""}) == launch_home


def test_two_profiles_are_isolated(tmp_path):
    home_a = tmp_path / ".hermes" / "profiles" / "a"
    home_b = tmp_path / ".hermes" / "profiles" / "b"
    assert _session_owner_home({"profile_home": str(home_a)}) == home_a
    assert _session_owner_home({"profile_home": str(home_b)}) == home_b
