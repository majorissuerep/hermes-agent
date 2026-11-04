"""Authorize user namespaces for one disposable CI launcher, retaining host policy.

Ubuntu requires an application-specific AppArmor userns allowance. Give only this
run's temporary launcher that allowance, and inherit it through the unchanged
unprivileged qualification tree. Never disable the global restriction or replace
an existing profile. This helper is CI infrastructure, not a product launcher.
"""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

from hermes_platform.resolver import locate_command

_CONTEXT = "HERMES_CI_NAMESPACE_CONTEXT"
_RESTRICTION = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
_PROFILE = re.compile(r"hermes-ci-userns-[0-9a-f]{32}")
_ATTACHMENT = re.compile(r"/[A-Za-z0-9._/-]+")


def _restriction() -> str | None:
    try:
        value = _RESTRICTION.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    if value not in {"0", "1"}:
        raise RuntimeError("Cannot determine the native AppArmor user-namespace restriction")
    return value


def _command(name: str) -> list[str]:
    command = locate_command(name).command
    if not command:
        raise RuntimeError(f"Native namespace qualification requires {name}")
    return command


def _identity() -> dict:
    return {"uid": os.getuid(), "euid": os.geteuid(), "gid": os.getgid(),
            "egid": os.getegid(), "groups": sorted(os.getgroups())}


def _label() -> str:
    return Path("/proc/self/attr/current").read_text(encoding="ascii").strip()


def _validate_context(raw: str) -> None:
    context = json.loads(raw)
    profile = context.get("profile", "")
    if not isinstance(profile, str) or not _PROFILE.fullmatch(profile):
        raise RuntimeError("Invalid native namespace qualification context")
    if _label() != f"{profile} (unconfined)":
        raise RuntimeError("The kernel did not apply this run's namespace profile")
    identity = _identity()
    if context.get("identity") != identity or identity["euid"] == 0:
        raise RuntimeError("Native qualification must retain its original unprivileged identity")
    if context.get("restriction") != "1" or _restriction() != "1":
        raise RuntimeError("The host namespace restriction changed during qualification")
    print(f"Native namespace permission: {profile}; host restriction=1; uid={identity['uid']}", flush=True)


@contextlib.contextmanager
def _authorized_launcher(*, env: dict[str, str]):
    """Remove only our new profile after success, failure, or a bounded child timeout."""
    before = _restriction()
    identity = _identity()
    if before != "1" or identity["euid"] == 0 or _label() != "unconfined":
        raise RuntimeError("An unprivileged restricted native worker is required")
    before_bytes = _RESTRICTION.read_bytes()
    profile = f"hermes-ci-userns-{uuid.uuid4().hex}"
    sudo, parser = _command("sudo"), _command("apparmor_parser")
    with tempfile.TemporaryDirectory(prefix="hermes-ci-userns-") as directory:
        root = Path(directory).resolve()
        launcher = root / "env"  # Multicall coreutils selects its applet from argv[0].
        if not _ATTACHMENT.fullmatch(str(launcher)):
            raise RuntimeError("Native qualification requires an exact ASCII attachment path")
        shutil.copyfile(_command("env")[0], launcher)
        launcher.chmod(0o700)
        policy = f'abi <abi/4.0>,\nprofile {profile} "{launcher}" flags=(unconfined) {{\n  userns,\n}}\n'
        subprocess.run([*sudo, "-n", *parser, "--add", "--skip-cache"],
                       input=policy, text=True, env=env, check=True, timeout=30)
        try:
            selected = dict(env)
            selected[_CONTEXT] = json.dumps({"profile": profile, "identity": identity,
                                             "restriction": before})
            yield launcher, selected
        finally:
            try:
                subprocess.run([*sudo, "-n", *parser, "--remove", "--skip-cache"],
                               input=policy, text=True, env=env, check=True, timeout=30)
            finally:
                if _RESTRICTION.read_bytes() != before_bytes or _identity() != identity:
                    raise RuntimeError("Native qualification changed the host namespace restriction")


def reexec_if_required() -> int | None:
    """Return a child exit status, or None when the current process should qualify."""
    raw = os.environ.get(_CONTEXT)
    if raw is not None:
        _validate_context(raw)
        return None
    if _restriction() != "1":
        return None
    with _authorized_launcher(env=dict(os.environ)) as (launcher, env):
        result = subprocess.run([str(launcher), sys.executable, "-m", "scripts.ci.native_checks"],
                                env=env, check=False, timeout=3300)
        return result.returncode
