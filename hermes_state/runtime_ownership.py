"""Local runtimes share a profile; destructive maintenance requires exclusive ownership.

Lock inodes must never be restored or unlinked: doing so creates a second owner.
An existing pre-terminal-only lock participates solely for safe upgrades from old installs.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
import threading

from hermes_platform.processes import release_file_lock, try_acquire_file_lock


class OwnershipConflict(RuntimeError):
    pass


def canonical_home(home: Path) -> Path:
    return Path(home).expanduser().resolve()


def _open_lock(path: Path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise PermissionError(f"Unsafe profile lock: {path}")
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, "r+b")
    except BaseException:
        os.close(fd)
        raise


class ProfileOwnership:
    def __init__(self, *, exclusive: bool = False):
        self._handles: dict[Path, list] = {}
        self._writers: list[threading.Thread] = []
        self._mutex = threading.RLock()
        self.exclusive = exclusive

    @property
    def homes(self) -> tuple[Path, ...]:
        with self._mutex:
            return tuple(self._handles)

    def reserve(self, homes) -> None:
        with self._mutex:
            added = []
            try:
                for home in sorted({canonical_home(path) for path in homes}):
                    if home in self._handles:
                        continue
                    home.mkdir(mode=0o700, parents=True, exist_ok=True)
                    handles = []
                    try:
                        paths = [home / "runtime.lock"]
                        legacy = home / "gateway.lock"
                        if legacy.exists() or legacy.is_symlink():
                            paths.append(legacy)
                        for path in paths:
                            handle = _open_lock(path)
                            handles.append(handle)
                            if not try_acquire_file_lock(handle, shared=not self.exclusive):
                                raise OwnershipConflict(f"Profile {home} is busy; close its Hermes sessions and retry")
                    except BaseException:
                        for handle in reversed(handles):
                            handle.close()
                        raise
                    self._handles[home] = handles
                    added.append(home)
            except BaseException:
                for home in reversed(added):
                    self.release(home)
                raise

    def owns(self, home: Path) -> bool:
        with self._mutex:
            return canonical_home(home) in self._handles

    def release(self, home: Path) -> None:
        with self._mutex:
            for handle in reversed(self._handles.pop(canonical_home(home), [])):
                release_file_lock(handle)
                handle.close()

    def start_writer(self, thread: threading.Thread) -> None:
        with self._mutex:
            self._writers.append(thread)
            thread.start()

    def close(self) -> None:
        with self._mutex:
            if any(thread.is_alive() for thread in self._writers):
                return
            self._writers.clear()
            for home in reversed(self.homes):
                self.release(home)


process_ownership = ProfileOwnership()
_maintenance = threading.local()


@contextmanager
def exclusive_maintenance(homes):
    owner = getattr(_maintenance, "owner", None)
    outermost = owner is None
    if outermost:
        owner = ProfileOwnership(exclusive=True)
    previous = set(owner.homes)
    try:
        owner.reserve(homes)
        if outermost:
            _maintenance.owner = owner
        yield
    finally:
        for home in reversed(owner.homes):
            if home not in previous:
                owner.release(home)
        if outermost:
            _maintenance.owner = None
