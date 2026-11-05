"""Native identity and bounded, credential-free execution of an already verified checker.

Selection and package provenance belong to PM/the consumer. This boundary never discovers,
installs, interprets source, or adds authority. Native same-UID code is outside its isolation
claim; changed executable/directory identities cannot produce an accepted checker result.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import selectors
import stat
import subprocess
import time
from dataclasses import dataclass

from hermes_platform.managed_supervision import SupervisedChild

STDOUT_LIMIT = 64_000
STDERR_LIMIT = 16_000
INPUT_LIMIT = 1_000_000


class CheckerProcessError(RuntimeError):
    """The native checker could not establish a bounded, unchanged execution."""


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_uid, value.st_mode,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _directory(path: Path) -> tuple[int, ...]:
    value = path.lstat()
    if not stat.S_ISDIR(value.st_mode) or path.resolve(strict=True) != path:
        raise CheckerProcessError("The checker directory is not a physical directory")
    # Directory size/times change when unrelated files are installed; its native inode,
    # owner and permissions are the relevant replacement boundary.
    return _identity(value)[:4]


@dataclass(frozen=True)
class CheckerExecutable:
    path: Path
    identity: tuple[int, ...]
    anchors: tuple[tuple[Path, tuple[int, ...]], ...]
    sha256: str

    def validate(self) -> None:
        if (self.path.resolve(strict=True) != self.path or _identity(self.path.lstat()) != self.identity
                or any(_directory(path) != value for path, value in self.anchors)):
            raise CheckerProcessError("The original verified checker changed")


def capture_executable(binary: Path, *, root: Path) -> CheckerExecutable:
    """Pin one PM-selected physical regular executable, without executing or resolving PATH."""
    binary, root = Path(binary), Path(root)
    if not binary.is_absolute() or not root.is_absolute() or not binary.is_relative_to(root):
        raise CheckerProcessError("The checker must be inside its selected absolute package root")
    fd = os.open(binary, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not os.access(binary, os.X_OK):
            raise CheckerProcessError("The selected checker is not a regular executable")
        digest = hashlib.sha256()
        with os.fdopen(os.dup(fd), "rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        if _identity(before) != _identity(os.fstat(fd)):
            raise CheckerProcessError("The checker changed while its identity was captured")
        selected = CheckerExecutable(binary, _identity(before),
            tuple((path, _directory(path)) for path in dict.fromkeys((root, binary.parent))), digest.hexdigest())
        selected.validate()
        return selected
    finally:
        os.close(fd)


def clean_environment(directory: Path) -> dict[str, str]:
    """No inherited credentials, grants, Lean options, HOME, PATH tools or session metadata."""
    return {"HOME": str(directory), "TMPDIR": str(directory), "LANG": "C.UTF-8"}


def _working_directory(path: Path) -> tuple[int, ...]:
    value = _directory(path)
    if value[2] != os.geteuid() or stat.S_IMODE(value[3]) != 0o700 or any(path.iterdir()):
        raise CheckerProcessError("The checker requires an empty owned 0700 working directory")
    return value


def _event(selector, key, payload: memoryview, outputs: dict) -> memoryview:
    stream, name = key.fileobj, key.data
    if name == "stdin":
        try:
            written = os.write(stream.fileno(), payload[:4096])
        except BrokenPipeError:
            written = len(payload)
        payload = payload[written:]
        if not payload:
            selector.unregister(stream)
            stream.close()
        return payload
    block = os.read(stream.fileno(), 8192)
    if not block:
        selector.unregister(stream)
        stream.close()
        return payload
    target, limit = outputs[name]
    if len(target) + len(block) > limit:
        raise CheckerProcessError("The checker exceeded its diagnostic output bound")
    target.extend(block)
    return payload


def _exchange(process, payload: bytes | None, timeout: float) -> tuple[bytes, bytes]:
    outputs = {"stdout": (bytearray(), STDOUT_LIMIT), "stderr": (bytearray(), STDERR_LIMIT)}
    deadline, pending = time.monotonic() + timeout, memoryview(payload or b"")
    with selectors.DefaultSelector() as selector:
        for name in ("stdout", "stderr", "stdin"):
            stream = getattr(process, name)
            if stream is None:
                continue
            if name == "stdin" and not pending:
                stream.close()
                continue
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_WRITE if name == "stdin" else selectors.EVENT_READ, name)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired("verified-checker", timeout)
            for key, _mask in selector.select(remaining):
                pending = _event(selector, key, pending, outputs)
    return bytes(outputs["stdout"][0]), bytes(outputs["stderr"][0])


def run(selected: CheckerExecutable, arguments: list[str], *, directory: Path,
        source: str | None = None, timeout: float = 30) -> subprocess.CompletedProcess:
    """Anonymous-pipe I/O, bounded output, no inherited key descriptors, supervised cancellation."""
    selected.validate()
    working_identity = _working_directory(directory)
    data = None if source is None else source.encode("utf-8")
    if data is not None and len(data) > INPUT_LIMIT:
        raise CheckerProcessError("Generated checker source exceeds its input bound")
    argv = [str(selected.path), *arguments]
    child = SupervisedChild(argv, cwd=directory, env=clean_environment(directory),
                            stdin=subprocess.DEVNULL if data is None else subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        started = time.monotonic()
        stdout, stderr = _exchange(child.process, data, timeout)
        code = child.wait(timeout=max(0.01, timeout - (time.monotonic() - started)))
        selected.validate()
        if _directory(directory) != working_identity or any(directory.iterdir()):
            raise CheckerProcessError("The stdin-only checker wrote unexpected files")
        return subprocess.CompletedProcess(argv, code, stdout.decode("utf-8", "strict"),
                                           stderr.decode("utf-8", "strict"))
    except BaseException as error:
        if child.cancel() is None:
            raise CheckerProcessError("The checker stopped responding; native cleanup is unconfirmed") from error
        raise
    finally:
        for stream in (child.process.stdin, child.process.stdout, child.process.stderr):
            if stream is not None:
                stream.close()
