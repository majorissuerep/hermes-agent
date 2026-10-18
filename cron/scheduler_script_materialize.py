"""Private execution views for vaulted cron scripts and their sibling assets."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import stat
import tempfile


@contextmanager
def script_execution_path(path: Path, scripts_root: Path):
    """Yield an executable local view; vaulted source is never rewritten in plaintext.

    Source-relative reads/imports see a private copy of the scripts tree. Explicit
    job workdirs remain unchanged; otherwise execution uses the copied script parent.
    """
    from hermes_security.io import _home_for, read_bytes

    if _home_for(path) is None:
        yield path
        return
    root = scripts_root.resolve()
    relative = path.resolve().relative_to(root)
    temp_root = Path(tempfile.gettempdir()).resolve()
    owner = _home_for(path)
    if owner == temp_root or owner in temp_root.parents:
        raise RuntimeError("Cron script scratch directory must be outside durable vault state")
    with tempfile.TemporaryDirectory(prefix="hermes-cron-script-", dir=temp_root) as scratch:
        view = Path(scratch)
        total = 0
        for count, source in enumerate(root.rglob("*"), 1):
            if count > 4096:
                raise ValueError("Cron script execution view exceeds 4096 assets")
            resolved = source.resolve()
            resolved.relative_to(root)  # reject symlink escapes, including sibling assets
            target = view / source.relative_to(root)
            if source.is_symlink():
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                target.symlink_to(view / resolved.relative_to(root), target_is_directory=source.is_dir())
                continue
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            if not source.is_file():
                raise ValueError(f"Unsupported cron script asset: {source}")
            if source.stat().st_size > 16 * 1024 * 1024 - total:
                raise ValueError("Cron script execution view exceeds 16 MiB")
            content = read_bytes(source, purpose="state")
            if content is None:
                raise FileNotFoundError(source)
            total += len(content)
            if total > 16 * 1024 * 1024:
                raise ValueError("Cron script execution view exceeds 16 MiB")
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(content)
            target.chmod(0o700 if source.stat().st_mode & stat.S_IXUSR else 0o600)
        yield view / relative
