"""macOS backend: a Seatbelt (SBPL) profile run through ``/usr/bin/sandbox-exec``.

Seatbelt is the kernel sandbox Chromium, Codex CLI and Claude Code confine child
processes with. ``sandbox-exec`` is deprecated as a *public API* but ships on every
macOS release; the profile is inherited by all descendants and cannot be lifted.

The base rules follow Chromium's renderer policy: deny by default, allow fork/exec,
signals and process-info only within the same sandbox, the sysctls libSystem reads,
and the handful of Mach services libc needs (user lookup, power management for
Python's SemLock). Seatbelt matches *resolved* paths (``/tmp`` is ``/private/tmp``),
so every grant is realpath'd here.
"""

from __future__ import annotations

import os
from typing import Mapping

SANDBOX_EXEC = "/usr/bin/sandbox-exec"

_BASE = """\
(version 1)
(deny default)
(allow process-exec)
(allow process-fork)
(allow signal (target same-sandbox))
(allow process-info* (target same-sandbox))
(allow user-preference-read)
(allow file-read-metadata)
(allow sysctl-read)
(allow iokit-open (iokit-registry-entry-class "RootDomainUserClient"))
(allow mach-lookup (global-name "com.apple.system.opendirectoryd.libinfo")
                   (global-name "com.apple.PowerManagement.control")
                   (global-name "com.apple.system.notification_center")
                   (global-name "com.apple.logd"))
(allow ipc-posix-sem)
(allow ipc-posix-shm-read-data (ipc-posix-name "apple.shm.notification_center"))
(allow pseudo-tty)
(allow file-read* file-write* file-ioctl (literal "/dev/ptmx") (regex #"^/dev/ttys[0-9]+$"))
(allow file-read* (literal "/") (literal "/private") (literal "/private/var")
                  (subpath "/private/var/select") (subpath "/System/Volumes/Preboot/Cryptexes")
                  (subpath "/private/var/db/dyld") (literal "/dev/fd") (subpath "/dev/fd"))
(allow file-write-data (literal "/dev/dtracehelper"))
"""

_NETWORK = """\
(allow system-socket)
(allow network-outbound (remote ip))
(allow network-inbound (local ip))
(allow network-bind (local ip))
(allow network-outbound (literal "/private/var/run/mDNSResponder"))
(allow mach-lookup (global-name "com.apple.dnssd.service")
                   (global-name "com.apple.trustd")
                   (global-name "com.apple.trustd.agent")
                   (global-name "com.apple.SecurityServer")
                   (global-name "com.apple.networkd"))
"""

_UNIX_SOCKETS = """\
(allow system-socket)
(allow network-outbound (remote unix-socket))
(allow network-bind (local unix-socket))
"""


def _q(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _real(path: str) -> str:
    return os.path.realpath(path)


def build_profile(spec: Mapping) -> str:
    """Pure: the SBPL profile for a launch spec (see ``SandboxPolicy.launch_spec``)."""
    parts = [_BASE]
    reads = [p for p in dict.fromkeys(_real(p) for p in spec.get("read", ()))]
    writes = [p for p in dict.fromkeys(_real(p) for p in spec.get("write", ()))]
    if reads:
        parts.append("(allow file-read* " + " ".join(f"(subpath {_q(p)})" for p in reads) + ")\n")
    if writes:
        parts.append("(allow file-read* file-write* " + " ".join(f"(subpath {_q(p)})" for p in writes) + ")\n")
    if spec.get("network"):
        parts.append(_NETWORK)
    if spec.get("unix_sockets"):
        parts.append(_UNIX_SOCKETS)
    return "".join(parts)


def wrap(argv: list[str], spec: Mapping) -> list[str]:
    return [SANDBOX_EXEC, "-p", build_profile(spec), *argv]


def available() -> tuple[bool, str]:
    if os.access(SANDBOX_EXEC, os.X_OK):
        return True, "Seatbelt via sandbox-exec"
    return False, f"{SANDBOX_EXEC} is missing"
