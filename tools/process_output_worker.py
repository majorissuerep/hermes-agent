"""Lifetime-owned encrypted stdout sink for one durable native process.

The launcher may exit, but this process retains the adopted key and the child's
stdout pipe until EOF. No plaintext output is materialized in the profile.
"""

from __future__ import annotations

import os
from pathlib import Path
import select
import signal
import subprocess
import sys


def _drain(child, target, frames) -> None:
    """Do not wait forever for an orphaned grandchild holding stdout open."""
    drained = 0
    while True:
        alive = child.poll() is None
        readable, _, _ = select.select([child.stdout], [], [], 0.2)
        if not readable:
            if not alive:
                break
            continue
        chunk = os.read(child.stdout.fileno(), 65536)
        if not chunk:
            break
        frames.append(target, chunk, purpose="log")
        drained += 0 if alive else len(chunk)
        if drained >= 1 << 20:
            break


def main() -> int:
    # This private entry is invoked by absolute path, including from a foreign cwd.
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from pm.environments import activate_dependencies

    activate_dependencies(root)
    from hermes_security import frames, io as state_io
    from hermes_security.handoff import adopt_inherited_key
    from hermes_security.session_authority import start_session_authority
    from hermes_security.vault import get_vault
    from hermes_constants import get_hermes_home
    from hermes_state.runtime_ownership import ProfileOwnership
    from hermes_platform.child_processes import register_self
    from tools.environments.local import build_subprocess_env

    log, ready_fd, execution_id, *argv = sys.argv[1:]
    ready_fd = int(ready_fd)
    authority = None
    ownership = ProfileOwnership()
    try:
        if not adopt_inherited_key():
            raise RuntimeError("durable process requires an unlocked vault")
        home = get_hermes_home()
        ownership.reserve([home])
        get_vault(home)  # Verify exact-home authority before executing the command.
        target = Path(log)
        if target.parent != home / "logs" / "process-output":
            raise ValueError("durable output path does not belong to this profile")
        frames.append(target, b"", purpose="log")
        if not register_self("process-output"):
            raise RuntimeError("durable process could not register its native identity")
        authority = start_session_authority(home, execution_id)
        # The launcher already applied the terminal's skill-aware secret filter.
        # Preserve deliberate passthrough while binding this worker's owning home.
        child_env = build_subprocess_env(scrub_secrets=False, strip_launch_profile=True)
        if authority is not None:
            child_env.update(authority.environment())
        with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, env=child_env) as child:
            os.write(ready_fd, b"R")
            os.close(ready_fd)
            ready_fd = -1
            try:
                _drain(child, target, frames)
            except BaseException:
                # A failed sink must not leave a command writing unrecorded user output.
                if os.getpgrp() == os.getpid():
                    os.killpg(os.getpgrp(), signal.SIGKILL)
                child.kill()
                raise
            code = child.wait()  # health: allow HX006 -- durable command lifetime is user-controlled; verified process-tree cancellation owns termination
            state_io.write_json(target.with_suffix(".exit"), code, purpose="state")
            return code if code >= 0 else 128 - code
    finally:
        if ready_fd >= 0:
            os.close(ready_fd)
        if authority is not None:
            authority.close()
        ownership.close()


if __name__ == "__main__":
    raise SystemExit(main())
