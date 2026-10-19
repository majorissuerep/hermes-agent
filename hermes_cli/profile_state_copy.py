"""Authenticated profile-file copies; encrypted bytes never retain another path's AAD."""
from pathlib import Path
import shutil


def copy_profile_file(source: Path, destination: Path, *, purpose: str) -> None:
    from hermes_security import io
    from hermes_security.errors import PlaintextStateError

    destination.parent.mkdir(parents=True, exist_ok=True)
    source_owner, destination_owner = io._home_for(source), io._home_for(destination)
    if source_owner is None and destination_owner is None:
        # Pre-vault profile bootstrap remains supported; no encrypted credential is downgraded.
        shutil.copy2(source, destination)
        return
    if destination_owner is None:
        raise PlaintextStateError("Initialize the destination profile vault before copying encrypted state")
    content = io.read_bytes(source, purpose=purpose)
    if content is None:
        raise FileNotFoundError(source)
    io.write_bytes(destination, content, purpose=purpose)
