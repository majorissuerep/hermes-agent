"""Owner-only scratch directories that exist only on a proven RAM-only mount.

The base is the effective (encrypted) config ``security.protected_work_dir`` when that key is set,
otherwise the per-user runtime directory (``XDG_RUNTIME_DIR``) as a candidate. Either way the base
must be proven RAM-only before anything is created. A configured base never falls back, and
nothing here picks ``/tmp``, ``/dev/shm`` or the Hermes cache.
"""
from __future__ import annotations

import contextlib
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

from hermes_platform.memory_protection import RamProof, per_user_runtime_dir, ram_only_directory_proof
from hermes_security.errors import VaultError

_PURPOSE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")

Prover = Callable[[Path], RamProof]


class ProtectedWorkspaceUnavailable(VaultError):
    """No proven RAM-only workspace could be created; the sensitive work must not start."""


def protected_workspace_base() -> Path:
    """The canonical base: configured ``security.protected_work_dir``, else ``XDG_RUNTIME_DIR``.

    Resolution only; the caller still proves the base. A configured value that is not an absolute
    path is refused rather than replaced by the runtime directory.
    """
    from hermes_cli.config_effective import load_user_config_effective

    security = load_user_config_effective(fail_closed=True).get("security")
    configured = security.get("protected_work_dir") if isinstance(security, dict) else None
    if configured is not None:
        expanded = os.path.expanduser(configured) if isinstance(configured, str) else ""
        if not os.path.isabs(expanded):
            raise ProtectedWorkspaceUnavailable("security.protected_work_dir must be an absolute directory path")
        return Path(expanded)
    candidate = per_user_runtime_dir()
    if candidate is None:
        raise ProtectedWorkspaceUnavailable(
            "No protected workspace base: set security.protected_work_dir to a RAM-only directory "
            "(no per-user runtime directory is available)"
        )
    return Path(candidate)


def protected_workspace_status(
    base: Path | str | None = None, *, prove: Prover = ram_only_directory_proof
) -> RamProof:
    """Report whether ``base`` (default: the canonical base) could host a workspace, creating nothing."""
    return prove(Path(os.path.realpath(protected_workspace_base() if base is None else base)))


def _require_proven(path: Path, prove: Prover, what: str) -> None:
    proof = prove(path)
    if not proof.proven:
        raise ProtectedWorkspaceUnavailable(f"{what} is not on a RAM-only mount: {proof.reason}")


def _verify_created(workspace: Path, prove: Prover) -> None:
    fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        created = os.fstat(fd)
        owner_only = stat.S_ISDIR(created.st_mode) and stat.S_IMODE(created.st_mode) in {0o700, 0o2700}
        if not owner_only or created.st_uid != os.geteuid() or os.path.realpath(workspace) != str(workspace):
            raise ProtectedWorkspaceUnavailable("The protected workspace changed while it was created")
        # mkdtemp's 0700 mode can inherit SGID from a group-managed runtime root.
        # Clear that bit only on the fresh pinned owner-only inode, never the base.
        if stat.S_IMODE(created.st_mode) == 0o2700:
            os.fchmod(fd, 0o700)
        current = workspace.lstat()
        if ((current.st_dev, current.st_ino) != (created.st_dev, created.st_ino)
                or stat.S_IMODE(current.st_mode) != 0o700):
            raise ProtectedWorkspaceUnavailable("The protected workspace changed while it was created")
    finally:
        os.close(fd)
    _require_proven(workspace, prove, "The created workspace")


@contextlib.contextmanager
def protected_workspace(
    purpose: str, *, base: Path | str | None = None, prove: Prover = ram_only_directory_proof
) -> Iterator[Path]:
    """Yield a fresh 0700 directory under a proven RAM-only base; remove it on exit.

    ``base`` defaults to :func:`protected_workspace_base`.
    """
    if not _PURPOSE.fullmatch(purpose):
        raise ValueError("A protected workspace purpose is a short lowercase [a-z0-9-] label")
    real_base = Path(os.path.realpath(protected_workspace_base() if base is None else base))
    _require_proven(real_base, prove, "The protected workspace base")
    workspace = Path(tempfile.mkdtemp(dir=real_base, prefix=f"hermes-{purpose}-"))
    try:
        _verify_created(workspace, prove)
        yield workspace
    finally:
        _remove(workspace)


def _remove(workspace: Path) -> None:
    """Remove the tree; a vanished workspace is fine, every other failure propagates."""
    with contextlib.suppress(FileNotFoundError):
        if workspace.is_symlink():
            workspace.unlink()  # swapped in by a racer: drop the link, never follow it
        else:
            shutil.rmtree(workspace)
