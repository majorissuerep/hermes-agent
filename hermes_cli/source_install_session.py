"""Keep an encrypted source install unlocked across its remaining shell stages."""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import subprocess
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run_install_session(command: list[str]) -> int:
    from hermes_constants import get_hermes_home
    from pm.state_io import prepare_owner, vault_home

    home = get_hermes_home().resolve()
    if vault_home(home / "config.yaml") is None:
        return subprocess.call(command, timeout=7200)

    # The PM owner can load crypto without the damaged venv's interpreter.
    # Keep its temporary authority alive until every installer stage exits.
    prepare_owner()
    from hermes_security.session_authority import start_session_authority

    session_id = "source-install-" + secrets.token_hex(16)
    authority = start_session_authority(home, session_id)
    if authority is None:
        raise RuntimeError("encrypted install has no unlocked vault owner")
    env = {**os.environ, "HERMES_SESSION_ID": session_id, **authority.environment()}
    try:
        return subprocess.call(command, env=env, timeout=7200)
    finally:
        authority.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: source_install_session.py COMMAND [ARG ...]")
    raise SystemExit(run_install_session(sys.argv[1:]))
