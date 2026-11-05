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


# --- Named long-lived workspaces --------------------------------------------------------------
# A browser launched on a profile outlives the call that prepared it and may be re-attached by a
# later cooperating local process, so its directory needs a stable name under the same proven base
# instead of a context-scoped mkdtemp. Every use re-proves the base and the directory.

_STABLE_NAME = re.compile(r"hermes-[a-z0-9][a-z0-9-]{0,40}-[0-9a-f]{16,64}")


def stable_workspace_path(name: str, base: Path | str) -> Path:
    """Where the named workspace lives under an already resolved base; creates and proves nothing."""
    if not _STABLE_NAME.fullmatch(name):
        raise ValueError("A stable protected workspace name is hermes-<purpose>-<hex digest>")
    return Path(os.path.realpath(base)) / name


def ensure_stable_workspace(path: Path, *, prove: Prover = ram_only_directory_proof) -> Path:
    """Create (0700) or re-validate a named workspace on a proven RAM-only base."""
    _require_proven(path.parent, prove, "The protected workspace base")
    with contextlib.suppress(FileExistsError):
        os.mkdir(path, 0o700)
    _verify_created(path, prove)
    return path


def fresh_sibling_workspace(path: Path, label: str, *, prove: Prover = ram_only_directory_proof) -> Path:
    """A new 0700 staging directory beside ``path`` (same proven base), for atomic publication."""
    if not _PURPOSE.fullmatch(label):
        raise ValueError("A staging label is a short lowercase [a-z0-9-] label")
    _require_proven(path.parent, prove, "The protected workspace base")
    staged = Path(tempfile.mkdtemp(dir=path.parent, prefix=f"{path.name}.{label}-"))
    try:
        _verify_created(staged, prove)
    except BaseException:
        _remove(staged)
        raise
    return staged


def remove_stable_workspace(path: Path) -> None:
    """Remove a named or staging workspace tree without following a swapped-in link."""
    _remove(path)
