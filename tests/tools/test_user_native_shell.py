"""User-native terminals: commands run under the USER's login shell.

Contract:
- resolve_user_shell: passwd entry first (authoritative even when $SHELL is
  stale/missing — systemd, cron), $SHELL fallback, bash last resort.
- Non-bash POSIX-family users get their shell executing the command, with
  cross-command export persistence via the env dump round-trip.
- bash users: byte-identical upstream behavior (eval path).
- SHELL is always exported to children (nested vim :!, ssh, tmux).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.environments import base_session_env as bse
from tools.environments.user_shell import (
    ensure_shell_env_var,
    is_posix_shell_family,
    resolve_user_shell,
)


def test_resolver_prefers_passwd_entry(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/bash")
    shell = resolve_user_shell()
    assert Path(shell).is_absolute()
    assert Path(shell).name in {"bash", "zsh", "sh", "dash", "fish"}


def test_resolver_rejects_missing_shell_binary(monkeypatch):
    monkeypatch.setenv("SHELL", "/opt/nonexistent/fish")
    assert resolve_user_shell() != "/opt/nonexistent/fish"


def test_resolver_rejects_nologin(monkeypatch):
    monkeypatch.setenv("SHELL", "/sbin/nologin")
    assert resolve_user_shell() != "/sbin/nologin"


def test_bash_user_gets_upstream_eval():
    script = bse._user_command_invocation("echo hi", "/bin/bash")
    assert script == "eval 'echo hi'"


def test_none_shell_gets_upstream_eval():
    assert bse._user_command_invocation("echo hi", None) == "eval 'echo hi'"


def test_zsh_user_gets_zsh_handoff_with_dump():
    script = bse._user_command_invocation("echo hi", "/usr/bin/zsh", "/tmp/envdump")
    assert script.startswith("/usr/bin/zsh -c 'echo hi")
    assert "export -p > /tmp/envdump" in script
    assert "eval" not in script


def test_dash_user_handoff():
    script = bse._user_command_invocation("echo hi", "/bin/dash", "/tmp/envdump")
    assert script.startswith("/bin/dash -c 'echo hi")


def test_wrap_command_bash_unchanged():
    """The full wrapper for a bash user contains no handoff machinery."""
    script = bse._wrap_command_script(
        "echo hi",
        quoted_cwd="/tmp",
        quoted_snap="/tmp/snap",
        snap_tmp_template="/tmp/snap.tmp.X",
        passthrough_names=(),
        snapshot_ready=True,
        cwd_marker="__M__",
        user_shell="/bin/bash",
    )
    assert "eval 'echo hi'" in script
    assert ".userenv" not in script


def test_wrap_command_zsh_handoff_present():
    script = bse._wrap_command_script(
        "echo hi",
        quoted_cwd="/tmp",
        quoted_snap="/tmp/snap",
        snap_tmp_template="/tmp/snap.tmp.X",
        passthrough_names=(),
        snapshot_ready=True,
        cwd_marker="__M__",
        user_shell="/usr/bin/zsh",
    )
    assert "/usr/bin/zsh -c 'echo hi" in script
    assert ".userenv" in script  # dump + source-back wired


def test_ensure_shell_env_var_no_clobber():
    env = {"SHELL": "/usr/bin/fish"}
    ensure_shell_env_var(env)
    assert env["SHELL"] == "/usr/bin/fish"


def test_ensure_shell_env_var_fills_missing(monkeypatch):
    monkeypatch.delenv("SHELL", raising=False)
    env = {}
    ensure_shell_env_var(env)
    assert Path(env["SHELL"]).is_absolute()


def test_posix_family_classification():
    assert is_posix_shell_family("/bin/bash")
    assert is_posix_shell_family("/usr/bin/zsh")
    assert is_posix_shell_family("/bin/dash")
    assert not is_posix_shell_family("/usr/bin/fish")
    assert not is_posix_shell_family("/sbin/nologin")


@pytest.mark.skipif(not Path("/usr/bin/zsh").exists(), reason="zsh not installed")
def test_live_zsh_session_e2e(monkeypatch, tmp_path):
    """Real end-to-end: a zsh login user's commands run under zsh with export
    persistence. Patches pwd so the resolver sees zsh as the account shell."""
    monkeypatch.setenv("HERMES_ALLOW_NO_VAULT", "1")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    import pwd

    class _Fake:
        pw_shell = "/usr/bin/zsh"

    monkeypatch.setattr(pwd, "getpwuid", lambda uid: _Fake())
    from tools.environments.local import LocalEnvironment

    env = LocalEnvironment(cwd=str(tmp_path), timeout=30)
    try:
        r = env.execute("ps -o comm= -p $$")
        assert "zsh" in (r.get("output") or ""), r
        env.execute("export E2EPERSIST=42")
        r2 = env.execute("echo E2EPERSIST=$E2EPERSIST")
        assert "42" in (r2.get("output") or ""), r2
    finally:
        try:
            env.cleanup()
        except Exception:
            pass
