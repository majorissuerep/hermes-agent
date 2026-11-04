"""Qualify protected managed children in a disposable Linux mount namespace."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

from hermes_platform.memory_protection import ram_only_directory_proof
from hermes_platform.resolver import locate_command

ROOT = Path(__file__).resolve().parents[2]
FILES = [
    "tests/hermes_platform/test_remote_workspace.py",
    "tests/hermes_cli/test_browser_recording_public_recovery.py",
    "tests/hermes_cli/test_management_public_cli.py",
    "tests/hermes_cli/test_managed_agents_public_cli.py",
    "tests/hermes_cli/test_managed_agent_supervision.py",
    "tests/hermes_cli/test_management_rotation_cli.py",
    "tests/hermes_cli/test_management_state_cli.py",
    "tests/hermes_cli/test_management_keyfile_cli.py",
    "tests/hermes_cli/test_management_secret_cli.py",
    "tests/hermes_cli/test_management_backup_cli.py",
    "tests/hermes_cli/test_managed_agents_codex_cli.py",
    "tests/hermes_cli/test_managed_agents_claude_cli.py",
    "tests/hermes_cli/test_managed_agents_native_cli.py",
    "tests/hermes_cli/test_managed_agents_credentials_cli.py",
    "tests/hermes_cli/test_managed_owner_death.py",
    "tests/hermes_cli/test_managed_foreground_cancel.py",
    "tests/hermes_cli/test_managed_child_descriptors.py",
    "tests/hermes_cli/test_managed_protected_scratch.py",
]


def command(name: str) -> list[str]:
    path = locate_command(name).command
    if not path:
        raise RuntimeError(f"Native protected-management qualification requires {name}")
    return path


def inside_namespace(workspace: Path, parent_namespace_fd: int) -> None:
    """Refuse host mount changes: an inherited namespace FD must differ from ours."""
    parent = os.readlink(f"/proc/self/fd/{parent_namespace_fd}")
    if parent == os.readlink("/proc/self/ns/mnt"):
        raise RuntimeError("Protected-management checks require a separate mount namespace")
    os.close(parent_namespace_fd)
    info = workspace.lstat()
    if (workspace.resolve() != workspace or not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid()
            or any(workspace.iterdir())):
        raise RuntimeError("Protected-management mount must be an empty owned private directory")
    mount, unmount = command("mount"), command("umount")
    subprocess.run([*mount, "--make-rprivate", "/"], check=True, timeout=30)
    subprocess.run([*mount, "-t", "ramfs", "-o", "mode=0700", "ramfs", str(workspace)],
                   check=True, timeout=30)
    try:
        proof = ram_only_directory_proof(workspace)
        if not proof.proven or proof.filesystem != "ramfs":
            raise RuntimeError("Private managed scratch is not proven never-swapped RAM")
        print("Native protected management: private mount namespace; verified ramfs; no host swap changes",
              flush=True)
        env = dict(os.environ, XDG_RUNTIME_DIR=str(workspace),
                   HERMES_TEST_FILE_TIMEOUT="600", HERMES_TEST_FILE_RETRIES="0")
        subprocess.run(["scripts/run_tests.sh", "-j", "2", "--file-timeout", "600",
                        "--file-retries", "0", "--fail-on-skip", *FILES],
                       cwd=ROOT, env=env, check=True, timeout=1800)
    finally:
        subprocess.run([*unmount, str(workspace)], check=True, timeout=30)


def main() -> None:
    if not sys.platform.startswith("linux"):
        raise SystemExit("Protected mount-namespace qualification requires native Linux")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--parent-namespace-fd", type=int)
    args = parser.parse_args()
    if args.workspace is not None or args.parent_namespace_fd is not None:
        if args.workspace is None or args.parent_namespace_fd is None:
            parser.error("both private child arguments are required")
        inside_namespace(args.workspace, args.parent_namespace_fd)
        return
    with tempfile.TemporaryDirectory(prefix="hermes-native-managed-") as directory:
        descriptor = os.open("/proc/self/ns/mnt", os.O_RDONLY)
        try:
            subprocess.run([
                *command("unshare"), "--user", "--map-root-user", "--mount", "--",
                sys.executable, "-m", "scripts.ci.protected_management_checks",
                "--workspace", str(Path(directory).resolve()),
                "--parent-namespace-fd", str(descriptor),
            ], cwd=ROOT, pass_fds=(descriptor,), check=True, timeout=1900)
        finally:
            os.close(descriptor)
    print("Native protected-management namespace and temporary mount cleaned up", flush=True)


if __name__ == "__main__":
    main()
