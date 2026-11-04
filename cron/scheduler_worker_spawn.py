"""Transfer one selected vault to an explicitly detached native cron worker."""
from __future__ import annotations

import os
import subprocess


def spawn_worker(popen, command, *, env, cwd, stderr, home, creationflags):
    from hermes_security.handoff import prepare_child_key
    from hermes_security.session_authority import ENV_NAME

    # A durable fire owns its adopted task after the finite dispatcher exits.
    # Its native key is transferred once; an ordinary operation grant is not a key.
    handoff = prepare_child_key(home, retain_session=False)
    worker_env = dict(env)
    worker_env.pop(ENV_NAME, None)
    descriptor = handoff[0] if handoff else None
    if handoff:
        worker_env.update(handoff[1])
    try:
        return popen(command, cwd=cwd, env=worker_env, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=stderr, start_new_session=True,
                     creationflags=creationflags, **({"pass_fds": (descriptor,)} if descriptor is not None else {}))
    finally:
        if descriptor is not None:
            os.close(descriptor)
