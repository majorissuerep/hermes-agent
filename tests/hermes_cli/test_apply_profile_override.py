"""Regression tests for _apply_profile_override HERMES_HOME guard (issue #22502).

When HERMES_HOME is set to the hermes root (e.g. systemd hardcodes
HERMES_HOME=/root/.hermes), _apply_profile_override must still read
active_profile and update HERMES_HOME to the profile directory.

When HERMES_HOME is already a profile directory (.../profiles/<name>),
_apply_profile_override must trust it and return without re-reading
active_profile (child-process inheritance contract).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest


@pytest.fixture(autouse=True)
def _platform_home(tmp_path, monkeypatch):
    monkeypatch.setattr("hermes_constants._get_platform_default_hermes_home", lambda: tmp_path / ".hermes")


def _run_apply_profile_override(
    tmp_path, monkeypatch, *, hermes_home: str | None, active_profile: str | None,
    argv: list[str] | None = None, extra_env: dict[str, str] | None = None,
    create_active_profile: bool = True,
):
    """Run _apply_profile_override in isolation.

    Returns the value of os.environ["HERMES_HOME"] after the call,
    or None if unset.
    """
    hermes_root = tmp_path / ".hermes"
    hermes_root.mkdir(parents=True, exist_ok=True)

    if active_profile is not None:
        (hermes_root / "active_profile").write_text(active_profile, encoding="utf-8")

    if create_active_profile and active_profile and active_profile != "default":
        (hermes_root / "profiles" / active_profile).mkdir(parents=True, exist_ok=True)
        (hermes_root / "profiles" / active_profile / "config.yaml").write_text(
            "{}\n", encoding="utf-8")  # identity marker

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    if hermes_home is not None:
        monkeypatch.setenv("HERMES_HOME", hermes_home)
    else:
        monkeypatch.delenv("HERMES_HOME", raising=False)

    monkeypatch.setattr(sys, "argv", argv or ["hermes", "gateway", "start"])

    # Scrub supervisor markers the host environment may carry (systemd-run
    # CI runners export INVOCATION_ID) so each test controls them explicitly.
    for var in (
        "HERMES_SUPERVISED_CHILD",
        "HERMES_S6_SUPERVISED_CHILD",
        "INVOCATION_ID",
        "HERMES_GATEWAY_EXTERNAL_SUPERVISOR",
    ):
        monkeypatch.delenv(var, raising=False)

    for key, value in (extra_env or {}).items():
        monkeypatch.setenv(key, value)

    from hermes_cli.main import _apply_profile_override
    _apply_profile_override()

    return os.environ.get("HERMES_HOME")


@pytest.mark.parametrize("argv", [
    ["hermes", "profile", "list"],
    ["hermes", "profile", "use", "default"],
    ["hermes", "uninstall"],
    ["hermes", "uninstall", "--dry-run"],
    ["hermes", "uninstall", "--help"],
])
@pytest.mark.parametrize("exported_home", [False, True])
def test_missing_sticky_profile_allows_recovery_commands(
    tmp_path, monkeypatch, capsys, argv, exported_home,
):
    root = tmp_path / ".hermes"
    result = _run_apply_profile_override(
        tmp_path, monkeypatch, hermes_home=str(root) if exported_home else None,
        active_profile="ray",
        create_active_profile=False, argv=argv,
    )

    assert result == str(root)
    assert "saved profile 'ray' no longer exists; running this recovery command" in capsys.readouterr().err
    if argv[1:3] == ["profile", "use"]:
        from hermes_cli.profile_cmd import cmd_profile

        cmd_profile(SimpleNamespace(profile_action="use", profile_name="default"))
        assert not (root / "active_profile").exists()
    else:
        assert (root / "active_profile").read_text(encoding="utf-8-sig") == "ray"


@pytest.mark.parametrize("argv, expect_hint", [
    (["hermes", "chat"], True),
    (["hermes", "uninstall", "--data"], True),
    (["hermes", "uninstall", "--dat", "--yes"], True),
    (["hermes", "uninstall", "--full", "--yes"], True),
    (["hermes", "uninstall", "--fu"], True),
    (["hermes", "uninstall", "--full", "--data"], True),
    (["hermes", "-p", "ray", "uninstall"], False),  # explicit -p keeps the create hint
])
def test_missing_profile_still_blocks_other_or_explicit_commands(
    tmp_path, monkeypatch, capsys, argv, expect_hint,
):
    with pytest.raises(SystemExit) as exc:
        _run_apply_profile_override(
            tmp_path, monkeypatch, hermes_home=str(tmp_path / ".hermes"),
            active_profile="ray", create_active_profile=False, argv=argv,
        )
    assert exc.value.code == 1
    assert ("hermes profile use default" in capsys.readouterr().err) is expect_hint


class TestApplyProfileOverrideHermesHomeGuard:
    """Regression guard for issue #22502.

    Verifies that HERMES_HOME pointing to the hermes root does NOT suppress
    the active_profile check, while HERMES_HOME already pointing to a
    profile directory IS trusted as-is.
    """

    def test_hermes_home_at_root_with_active_profile_is_redirected(
        self, tmp_path, monkeypatch
    ):
        """HERMES_HOME=/root/.hermes + active_profile=coder must redirect
        HERMES_HOME to .../profiles/coder.

        Bug scenario from #22502: systemd sets HERMES_HOME to the hermes root
        and the user switches to a profile via `hermes profile use`.
        Before the fix, the guard returned early and active_profile was ignored.
        """
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir(parents=True, exist_ok=True)

        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            hermes_home=str(hermes_root),
            active_profile="coder",
        )

        assert result is not None, "HERMES_HOME must be set after profile redirect"
        assert "profiles" in result, (
            f"Expected HERMES_HOME to point into profiles/ dir, got: {result!r}"
        )
        assert result.endswith("coder"), (
            f"Expected HERMES_HOME to end with 'coder', got: {result!r}"
        )


    @pytest.mark.platforms("posix")
    def test_sudo_explicit_profile_resolves_invoking_users_profile(self, tmp_path, monkeypatch):
        """sudo elias ... should resolve `-p elias` under SUDO_USER, not root."""
        root_home = tmp_path / "root"
        user_home = tmp_path / "home" / "hermes"
        profile_dir = user_home / ".hermes" / "profiles" / "elias"
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "config.yaml").write_text(
            "{}\n", encoding="utf-8")  # identity marker: a bare dir does not resolve
        (root_home / ".hermes").mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(Path, "home", lambda: root_home)
        monkeypatch.setenv("SUDO_USER", "hermes")
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
        monkeypatch.setattr(sys, "argv", ["hermes", "-p", "elias", "gateway", "install", "--system"])

        import pwd

        monkeypatch.setattr(pwd, "getpwnam", lambda name: SimpleNamespace(pw_dir=str(user_home)))

        from hermes_cli.main import _apply_profile_override, _resolve_sudo_user_profile_env
        _apply_profile_override()

        assert os.environ.get("HERMES_HOME") == str(profile_dir)
        assert sys.argv == ["hermes", "gateway", "install", "--system"]
        # Same identity gate as ``-p`` without sudo: a marker-less shell is not a profile.
        (user_home / ".hermes" / "profiles" / "ghost" / "cron").mkdir(parents=True)
        assert _resolve_sudo_user_profile_env("ghost") is None
