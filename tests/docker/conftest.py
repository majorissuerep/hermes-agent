"""Shared fixtures for docker-image integration tests.

Tests in this directory build the image with the current ``Dockerfile``
and exercise it via ``docker run``. They skip when Docker is unavailable
(e.g. on developer laptops without a daemon).

Override the image with ``HERMES_TEST_IMAGE`` env var to point at a pre-built
image (faster local iteration); otherwise the ``built_image`` fixture builds
the repo's Dockerfile once per session.

"""
from __future__ import annotations


import os
import shutil
import subprocess
import time
from collections.abc import Iterator

import pytest


OWNER_KEY_PROGRAM = '''
import os, pwd, secrets, subprocess, sys
from pathlib import Path
owner = pwd.getpwnam("hermes")
initial_uid, initial_gid = os.geteuid(), os.getegid()
if initial_uid == 0:
    os.setegid(owner.pw_gid)
    os.seteuid(owner.pw_uid)
from hermes_security import migrate, vault
home = Path(os.environ["HERMES_HOME"])
password = secrets.token_urlsafe(24)
assert migrate.migrate_home(home, password).ok
vault.unlock(home, password)
del password
if initial_uid == 0:
    os.seteuid(initial_uid)
    os.setegid(initial_gid)
from hermes_security.handoff import prepare_child_key
fd, additions = prepare_child_key()
try:
    result = subprocess.run(["/opt/hermes/bin/hermes", *sys.argv[1:]],
                            env={**os.environ, **additions}, pass_fds=(fd,),
                            capture_output=True, text=True, timeout=30, check=False)
finally:
    os.close(fd)
    vault.lock_now(home)
print(result.stdout, end="")
print(result.stderr, file=sys.stderr, end="")
raise SystemExit(result.returncode)
'''


IMAGE_TAG = os.environ.get("HERMES_TEST_IMAGE", "hermes-agent-harness:latest")


def _docker_available() -> bool:
    """Return True iff a docker CLI is on PATH and the daemon answers."""
    if shutil.which("docker") is None:
        return False
    try:
        r = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=5,
            check=False,
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def pytest_collection_modifyitems(config, items):
    """Apply docker-suite policy: timeout bump + skip on missing docker."""
    docker_ok = _docker_available()
    skip_docker = pytest.mark.skip(
        reason="Docker not available or daemon not running",
    )
    for item in items:
        if "tests/docker/" not in str(item.fspath).replace(os.sep, "/"):
            continue
        if not docker_ok:
            item.add_marker(skip_docker)


@pytest.fixture(scope="session")
def built_image() -> str:
    """Build the image once per test session.

    Override with ``HERMES_TEST_IMAGE`` env var to point at a pre-built
    image (faster local iteration).
    """
    if os.environ.get("HERMES_TEST_IMAGE"):
        return IMAGE_TAG
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", ".."),
    )
    result = subprocess.run(
        ["docker", "build", "-t", IMAGE_TAG, repo_root],
        capture_output=True, text=True, timeout=1200,
        check=False,
    )
    assert result.returncode == 0, (
        f"docker build failed:\n{result.stderr[-2000:]}"
    )
    return IMAGE_TAG


@pytest.fixture
def container_name(request) -> Iterator[str]:
    """Generate a unique container name and ensure cleanup on test exit."""
    safe = request.node.name.replace("[", "_").replace("]", "_")
    name = f"hermes-test-{safe}"
    yield name
    subprocess.run(
        ["docker", "rm", "-f", name],
        capture_output=True, timeout=10,
        check=False,
    )


# ---------------------------------------------------------------------------
# docker_exec — default to the unprivileged hermes user
# ---------------------------------------------------------------------------
#
# Background: every Hermes runtime path inside the container drops to UID
# 10000 (the ``hermes`` user) via ``s6-setuidgid hermes``. ``docker exec``
# without ``-u`` runs as root, which is **not** representative of how
# production code executes. PR #30136 review caught a real regression
# this way — ``Path('/proc/1/exe').resolve()`` works as root and silently
# fails (PermissionError swallowed) for hermes, so a test that ran as root
# couldn't catch a feature that was inert for the actual runtime user.
#
# Tests in this directory MUST exercise the realistic user context. The
# helpers below run every probe under ``-u hermes`` unless a specific
# test explicitly opts into ``user="root"`` (rare — e.g. inspecting
# /proc/1/exe itself, chowning a volume).
# ---------------------------------------------------------------------------


def docker_exec(
    container: str,
    *args: str,
    user: str = "hermes",
    timeout: int = 30,
    extra_docker_args: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    """Run a command inside ``container`` as ``user`` (default: hermes).

    Returns the CompletedProcess with text=True, capture_output=True.

    Pass ``user="root"`` only when the test specifically needs root
    capabilities (e.g. reading /proc/1/exe, manipulating ownership).
    Most tests should use the default.
    """
    cmd = ["docker", "exec", "-u", user, *extra_docker_args, container, *args]
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        check=False,
    )


def docker_exec_sh(
    container: str,
    command: str,
    *,
    user: str = "hermes",
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Run ``sh -c <command>`` inside the container as ``user``."""
    return docker_exec(
        container, "sh", "-c", command, user=user, timeout=timeout,
    )


def wait_for_container_ready(
    container: str,
    *,
    deadline_s: float = 30.0,
    interval_s: float = 0.25,
) -> None:
    """Wait for this boot's actual terminal bootstrap, not a removed service log."""
    started = subprocess.run(["docker", "inspect", "--format", "{{.State.StartedAt}}", container],
                             capture_output=True, text=True, timeout=5, check=True).stdout.strip()
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        r = subprocess.run(["docker", "logs", "--since", started, container],
                           capture_output=True, text=True, timeout=5, check=False)
        if r.returncode == 0 and "Setup complete; starting terminal command" in r.stdout:
            return
        time.sleep(interval_s)
    raise TimeoutError(
        f"container {container} did not finish cont-init within {deadline_s}s"
    )


def start_container(
    image: str,
    name: str,
    *env: str,
    cmd: str = "sleep infinity",
    timeout: int = 60,
) -> str:
    """Start a detached container and wait for cont-init to finish.

    Args:
        image: Docker image to run.
        name: Container name (cleanup is the caller's responsibility —
            typically handled by the ``container_name`` fixture).
        env: Env vars as ``KEY=VALUE`` strings, each passed via ``-e``.
        cmd: Container CMD (default ``sleep infinity``).
        timeout: ``docker run`` subprocess timeout.

    Returns the container name. Raises on ``docker run`` failure or if
    the container never finishes cont-init within 30s.
    """
    args = ["docker", "run", "-d", "--name", name]
    for e in env:
        args.extend(["-e", e])
    args.extend([image, *cmd.split()])
    subprocess.run(args, check=True, capture_output=True, timeout=timeout)
    wait_for_container_ready(name)
    return name


def restart_container(container: str, timeout: int = 60) -> None:
    """Restart and wait for the new StartedAt-bounded bootstrap output."""
    subprocess.run(
        ["docker", "restart", container],
        check=True, capture_output=True, timeout=timeout,
    )
    wait_for_container_ready(container)


def poll_container(
    container: str,
    probe: str,
    *,
    deadline_s: float = 30.0,
    interval_s: float = 0.5,
    user: str = "hermes",
) -> tuple[bool, str]:
    """Repeatedly run ``probe`` inside the container until it exits 0 or
    ``deadline_s`` elapses.

    Returns ``(success, last_stdout)``. Useful for waiting on a process
    to appear, a port to open, a file to contain a string, etc.
    """
    end = time.monotonic() + deadline_s
    last = ""
    while time.monotonic() < end:
        r = docker_exec_sh(container, probe, user=user, timeout=10)
        last = r.stdout
        if r.returncode == 0:
            return True, last
        time.sleep(interval_s)
    return False, last


def wait_for_path(
    container: str,
    path: str,
    *,
    kind: str = "f",
    deadline_s: float = 30.0,
    interval_s: float = 0.25,
) -> bool:
    """Poll ``test -<kind> <path>`` inside the container until success or timeout.

    ``kind`` is the ``test`` flag: ``'f'`` for file, ``'d'`` for directory,
    ``'e'`` for existence. Returns ``True`` on success, ``False`` on timeout.
    """
    return poll_container(
        container, f"test -{kind} {path}",
        deadline_s=deadline_s, interval_s=interval_s,
    )[0]


def wait_for_log(
    container: str,
    log_path: str,
    needle: str,
    *,
    deadline_s: float = 30.0,
    interval_s: float = 0.25,
) -> str:
    """Poll until a log file inside the container contains ``needle``.

    Returns the full log on success.
    """
    end = time.monotonic() + deadline_s
    last = ""
    while time.monotonic() < end:
        r = docker_exec_sh(
            container, f"cat {log_path} 2>/dev/null", timeout=5,
        )
        if r.returncode == 0:
            last = r.stdout
            if needle in last:
                return last
        time.sleep(interval_s)
    raise AssertionError(f"Didn't see `{needle}` in {log_path} within {deadline_s} in container {container}")



def wait_for_docker_logs(
    container: str, needle: str, *, deadline_s: float = 30.0, interval_s: float = 0.5,
) -> str:
    """Poll ``docker logs`` until ``needle`` appears or deadline expires.

    Returns the full docker logs on success.
    """
    end = time.monotonic() + deadline_s
    last = ""
    while time.monotonic() < end:
        r = subprocess.run(
            ["docker", "logs", container],
            capture_output=True, text=True, timeout=10,
            check=False,
        )
        last = r.stdout + r.stderr
        if needle in last:
            return last
        time.sleep(interval_s)
    raise AssertionError(f"Didn't see `{needle}` in docker logs within {deadline_s} in container {container}")
