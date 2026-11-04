"""Fixed Linux/Python3 operations on an already-proven outgoing RAM profile.

The caller fences the immutable target generation around this argv. No operation
connects/provisions a target, takes a vault key, or chooses a different pathname.
Tar is transport only; durable admission/encryption is on the owning host.
"""
from __future__ import annotations

import posixpath

_TAR = ("tar", "--format=posix", "--sort=name", "--owner=0", "--group=0", "--numeric-owner",
        "--mtime=@0", "--pax-option=delete=atime,delete=ctime,exthdr.name=%d/PaxHeaders/%f")
_PROGRAM = r'''
import ctypes, hashlib, json, os, shutil, signal, stat, subprocess, sys
if sys.version_info < (3, 11):
    raise RuntimeError("protected browser continuity requires Python 3.11 or later")
operation, path, maximum, expected, stale, binding_raw = sys.argv[1:]
maximum, stale = int(maximum), stale == "yes"
binding = json.loads(binding_raw) if binding_raw else None
BINDING = ".hermes-browser-binding.json"
def expired(_number, _frame):
    raise TimeoutError("profile transfer deadline reached")

signal.signal(signal.SIGALRM, expired)
signal.alarm(120)

PARENT = os.getpid()
def child_lifetime():
    # Isolated single-threaded helper; native Linux PDEATHSIG fences abrupt SSH/client death.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != PARENT:
        os._exit(98)

def pinned():
    if os.path.realpath(path) != path:
        raise ValueError("profile path was replaced")
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        os.close(fd)
        raise ValueError("profile owner/mode changed")
    return fd, (info.st_dev, info.st_ino)

def unchanged(fd, identity):
    current = os.stat(path, follow_symlinks=False)
    if (current.st_dev, current.st_ino) != identity:
        raise ValueError("profile directory was replaced")

def quiet(fd):
    try:
        info = os.stat("SingletonLock", dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stale or not stat.S_ISLNK(info.st_mode):
        raise ValueError("browser profile is busy or its owner is unknown")
    link = os.readlink("SingletonLock", dir_fd=fd)
    host, separator, pid = link.rpartition("-")
    if not separator or host != os.uname().nodename or not pid.isascii() or not pid.isdecimal():
        raise ValueError("browser owner identity is unknown")
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return
    raise ValueError("browser owner is live or inaccessible")

def bound(fd):
    if binding is None:
        return
    descriptor = os.open(BINDING, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
            raise ValueError("browser runtime binding is unsafe")
        raw = source.read(16385)
    if len(raw) > 16384 or json.loads(raw) != binding:
        raise ValueError("browser runtime binding was replaced; retained")

def claim(fd):
    if not binding or os.listdir(fd):
        raise ValueError("only a fresh empty profile can be claimed")
    descriptor = os.open(BINDING, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=fd)
    with os.fdopen(descriptor, "wb") as output:
        output.write(json.dumps(binding, sort_keys=True).encode())
        output.flush()
        os.fsync(output.fileno())

def tar_argv(fd):
    return TAR + ["--create", "--file=-", "--directory=/proc/self/fd/" + str(fd), "."]

def exported_hash(fd):
    digest, count = hashlib.sha256(), 0
    with subprocess.Popen(tar_argv(fd), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          stdin=subprocess.DEVNULL, pass_fds=(fd,), preexec_fn=child_lifetime) as child:
      try:
        while block := child.stdout.read(1048576):
            count += len(block)
            if count > maximum:
                child.kill()
                raise ValueError("profile transfer exceeds its bound")
            digest.update(block)
        if child.wait(timeout=120):
            raise ValueError("profile transfer failed")
      except BaseException:
        child.kill()
        child.wait(timeout=5)
        raise
    return digest.hexdigest()

def retire(fd, identity):
    quiet(fd)
    bound(fd)
    if exported_hash(fd) != expected:
        raise ValueError("profile changed after its verified checkpoint; every copy retained")
    unchanged(fd, identity)
    quiet(fd)
    bound(fd)
    if not shutil.rmtree.avoids_symlink_attacks:
        raise ValueError("descriptor-safe profile retirement is unavailable")
    for name in (entry for entry in os.listdir(fd) if entry != BINDING):
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            shutil.rmtree(name, dir_fd=fd)
        else:
            os.unlink(name, dir_fd=fd)
    if os.path.lexists("/proc/self/fd/" + str(fd) + "/" + BINDING):
        os.unlink(BINDING, dir_fd=fd)
    unchanged(fd, identity)
    if os.listdir(fd):
        raise ValueError("profile retirement incomplete")

def run():
    fd, identity = pinned()
    try:
        if operation != "binding":
            quiet(fd)
        if operation == "claim":
            claim(fd)
        elif operation == "inspect":
            bound(fd)
            entries = set(os.listdir(fd)) - ({BINDING} if binding else set())
            print(json.dumps({"empty": not entries}))
        elif operation == "binding":
            bound(fd)
            print("true")
        elif operation == "export":
            bound(fd)
            result = subprocess.run(tar_argv(fd), stdin=subprocess.DEVNULL, stdout=sys.stdout.buffer,
                                    stderr=subprocess.DEVNULL, pass_fds=(fd,), preexec_fn=child_lifetime,
                                    timeout=120, check=False)
            if result.returncode:
                raise ValueError("profile export failed; original retained")
        elif operation == "restore":
            bound(fd)
            if set(os.listdir(fd)) - ({BINDING} if binding else set()):
                raise ValueError("profile restore requires an empty directory")
            result = subprocess.run(["tar", "--extract", "--file=-", "--no-same-owner", "--no-same-permissions",
                                     "--directory=/proc/self/fd/" + str(fd)],
                                    stdin=sys.stdin.buffer, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    pass_fds=(fd,), preexec_fn=child_lifetime,
                                    timeout=120, umask=0o077, check=False)
            if result.returncode:
                raise ValueError("profile restore incomplete; recovery archive retained")
        elif operation in ("retire", "retire-legacy"):
            retire(fd, identity)
            if operation == "retire-legacy":
                os.rmdir(path)  # explicit legacy conversion only; the verified empty original inode
        else:
            raise ValueError("unsupported profile operation")
        if operation != "retire-legacy":
            unchanged(fd, identity)
        if operation not in ("retire", "retire-legacy"):
            bound(fd)
        if operation != "binding":
            quiet(fd)
    finally:
        os.close(fd)

try:
    run()
except Exception as error:
    print("Outgoing browser profile " + operation + " refused: " + str(error), file=sys.stderr)
    raise SystemExit(1)
'''


def command(operation: str, path: str, *, maximum: int, expected: str = "", stale: bool = False,
            binding: dict | None = None) -> list[str]:
    """A fixed operation; the caller supplies a generated/proven profile path, never shell text."""
    if operation not in {"inspect", "binding", "export", "restore", "retire", "retire-legacy", "claim"} or maximum <= 0:
        raise ValueError("Invalid outgoing profile operation")
    if not posixpath.isabs(path) or "\x00" in path or any(part == ".." for part in path.split("/")):
        raise ValueError("Outgoing profile operations require a proven absolute directory")
    if operation in {"retire", "retire-legacy"} and (len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected)):
        raise ValueError("Retirement requires the verified export identity")
    program = "TAR = " + repr(list(_TAR)) + "\n" + _PROGRAM
    import json
    encoded = "" if binding is None else json.dumps(binding, sort_keys=True)
    if len(encoded) > 16384:
        raise ValueError("Browser runtime binding exceeds its bound")
    return ["python3", "-I", "-c", program, operation, path, str(maximum), expected,
            "yes" if stale else "no", encoded]


def existence_command(path: str) -> list[str]:
    """Read-only legacy inventory: a link also counts as present and is never silently ignored."""
    if not posixpath.isabs(path) or "\x00" in path or ".." in path.split("/"):
        raise ValueError("Legacy inventory requires an absolute selected path")
    return ["python3", "-I", "-c", "import json,os,sys; print(json.dumps(os.path.lexists(sys.argv[1])))", path]
