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
    from hermes_cli.source_install_log import LOG_FD_ENV, encrypted_install_log
    from pm.state_io import prepare_owner, vault_home

    home = get_hermes_home().resolve()
    if vault_home(home / "config.yaml") is None:
        env = {key: value for key, value in os.environ.items() if key != LOG_FD_ENV}
        return subprocess.call(command, env=env, timeout=7200)

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
        with encrypted_install_log(home, env) as pass_fds:
            return subprocess.call(command, env=env, timeout=7200, pass_fds=pass_fds)
    finally:
        authority.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: source_install_session.py COMMAND [ARG ...]")
    raise SystemExit(run_install_session(sys.argv[1:]))
