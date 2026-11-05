"""Non-owning execution identity for explicit recovery of an authenticated old target.

This is not a terminal environment: it cannot provision or retire a backend. Keep
the selected real owner alive while in use, but never forward its mutable target
selection or resource lifecycle methods.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _Identity:
    backend: str
    executable: str
    identifier: str
    image: str
    started: bool


class CapturedExecution:
    def __init__(self, identity: _Identity, owner, lock):
        self._identity, self._owner = identity, owner
        self._hermes_browser_profile_lock = lock
        self._hermes_browser_profile_retired = vars(owner).get("_hermes_browser_profile_retired", False)
        home = vars(owner).get("_hermes_browser_owner_home")
        if home is not None:
            self._hermes_browser_owner_home = home

    @property
    def backend(self):
        return self._identity.backend

    @property
    def _container_id(self):
        return self._identity.identifier if self.backend == "docker" else None

    @property
    def instance_id(self):
        return self._identity.identifier if self.backend == "singularity" else None

    @property
    def _image(self):
        return self._identity.image

    @property
    def image(self):
        return self._identity.image

    @property
    def _docker_exe(self):
        return self._identity.executable

    def prefix(self, *, user=None, interactive=True):
        original = self._identity
        if original.backend == "docker":
            argv = [original.executable, "exec"]
            if interactive:
                argv.append("-i")
            if user:
                argv += ["-u", user]
            return [*argv, original.identifier]
        if not original.started:
            return None
        return [original.executable, "exec", "instance://" + original.identifier]


def capture_execution(owner, backend: str, identifier: str, *, lock) -> CapturedExecution:
    """Bind an authenticated origin to this selected backend's existing client/image only."""
    from tools.environments.docker import DockerEnvironment
    from tools.environments.singularity import SingularityEnvironment

    if not isinstance(identifier, str) or not identifier or len(identifier) > 256 or "\x00" in identifier:
        raise ValueError("Original outgoing backend identity is missing; every copy retained")
    if backend == "docker" and isinstance(owner, DockerEnvironment):
        identity = _Identity(backend, owner._docker_exe, identifier, str(owner._image), True)
    elif backend == "singularity" and isinstance(owner, SingularityEnvironment):
        identity = _Identity(backend, owner.executable, identifier, str(owner.image), owner._instance_started)
    else:
        raise ValueError("Selected outgoing backend differs from the original recovery owner; retained")
    return CapturedExecution(identity, owner, lock)
