"""Atomic profile identity writes, including staged profile publication."""
from pathlib import Path


def write_profile_soul(profile_dir: Path, content: str, *, destination: Path | None = None) -> None:
    from hermes_security import io

    path = profile_dir / "SOUL.md"
    owner = io._home_for(path.parent)
    if owner is None:
        from utils import atomic_write_text
        atomic_write_text(path, content, preserve_mode=True, create_mode=0o644)
        return
    from hermes_security.vault import get_vault, _atomic_write
    target = (destination or profile_dir).resolve() / "SOUL.md"
    # A staged profile is renamed before consumers see it. Bind ciphertext to
    # the published identity path, not the temporary staging directory name.
    rel = target.relative_to(owner).as_posix()
    data = get_vault(owner).encrypt(content.encode("utf-8"), purpose="state", relpath=rel)
    _atomic_write(path, data)
