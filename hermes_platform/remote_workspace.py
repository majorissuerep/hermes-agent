"""Owner-only directories inside an outgoing execution backend, proven to be never-swapped RAM.

The control host's ``security.protected_work_dir`` and mount table say nothing about a container
or SSH target, so the target reports its own facts. One fixed POSIX ``sh`` probe (``_PROBE``) runs
through the backend's existing exec transport and prints NUL-framed, size-bounded facts: the
target uid, candidate directories' physical paths and modes, ``/proc/self/mountinfo`` and
``/proc/swaps``. The control host decides with the same rule as its own protected workspace
(:func:`hermes_platform.memory_protection.prove_ram_only`). The probe receives directory names
only; its report holds no page, credential or session data and is never logged. Nothing here
changes mounts or swap settings, and an unproven target is refused, never substituted.
"""
from __future__ import annotations

import posixpath
import re
import secrets
import stat
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Sequence

from hermes_platform.memory_protection import RamProof, parse_swaps, prove_ram_only
from hermes_platform.remote_identity import RuntimeChanged, RuntimeIdentity, capture

MOUNTINFO_LIMIT = 512 * 1024
SWAPS_LIMIT = 64 * 1024
_REPORT_LIMIT = MOUNTINFO_LIMIT + SWAPS_LIMIT + 64 * 1024
_PROBE_TIMEOUT = 20
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")

# $1 nonce, $2 mode (facts|provision), $3/$4 read bounds (limit + 1 detects truncation).
# facts: $5.. extra candidate bases after the target's own $XDG_RUNTIME_DIR.
# provision: $5 root, $6.. child names; creates only owner-only directories, never follows a link.
_PROBE = r"""
n=$1 mode=$2 ml=$3 sl=$4; shift 4
u=$(id -u)
printf '%s\0uid\0%s\0' "$n" "$u"
if [ "$mode" = facts ]; then
  for c in "${XDG_RUNTIME_DIR:-}" "$@"; do
    r= m=
    case $c in /*) r=$(cd "$c" 2>/dev/null && pwd -P) && m=$(stat -c %a "$r" 2>/dev/null) ;; esac
    printf 'cand\0%s\0%s\0%s\0' "$c" "$r" "$m"
  done
else
  d=$1; shift
  st=ok
  for s in "" "$@"; do
    p=$d${s:+/$s}
    if [ "$mode" = provision ]; then
      [ -e "$p" ] || [ -L "$p" ] || (umask 077 && mkdir "$p") 2>/dev/null
    fi
    if [ -L "$p" ] || [ ! -d "$p" ] || [ "$(stat -c %u "$p" 2>/dev/null)" != "$u" ]; then st="refused:$s"; break; fi
    if [ "$mode" = provision ]; then
      chmod u=rwx,go=,a-s,a-t "$p" 2>/dev/null || { st="chmod:$s"; break; }
    fi
    printf 'dir\0%s\0%s\0%s\0' "$s" "$(cd "$p" 2>/dev/null && pwd -P)" "$(stat -c %u:%a "$p" 2>/dev/null)"
  done
  printf 'state\0%s\0' "$st"
fi
printf 'mountinfo\0'; head -c "$ml" /proc/self/mountinfo 2>/dev/null; printf '\0'
printf 'swaps\0'; head -c "$sl" /proc/swaps 2>/dev/null; printf '\0'
"""

_ARITY = {"uid": 1, "cand": 3, "dir": 3, "state": 1, "mountinfo": 1, "swaps": 1}


class ProtectedTargetUnavailable(RuntimeError):
    """The selected backend could not prove an owner-only never-swapped RAM directory."""


@dataclass(frozen=True)
class ProtectedTargetDir:
    """A proven directory inside the backend and its proven owner-only children (backend paths)."""

    root: str
    children: dict[str, str]
    proof: RamProof
    identity: RuntimeIdentity


@dataclass
class _Report:
    uid: str = ""
    candidates: list[tuple[str, str, str]] = field(default_factory=list)
    dirs: dict[str, tuple[str, str]] = field(default_factory=dict)
    state: str = ""
    mountinfo: str | None = None  # None: missing or larger than the probe bound
    swap: str = "unknown"


def _text(value: bytes) -> str:
    return value.decode("utf-8", "surrogateescape")


def _apply(report: _Report, tag: str, values: list[bytes]) -> None:
    handlers = {
        "uid": lambda: setattr(report, "uid", _text(values[0])),
        "cand": lambda: report.candidates.append(tuple(map(_text, values))),
        "dir": lambda: report.dirs.__setitem__(_text(values[0]), tuple(map(_text, values[1:]))),
        "state": lambda: setattr(report, "state", _text(values[0])),
        "mountinfo": lambda: setattr(report, "mountinfo",
            _text(values[0]) if values[0] and len(values[0]) <= MOUNTINFO_LIMIT else None),
        "swaps": lambda: setattr(report, "swap",
            parse_swaps(_text(values[0])) if len(values[0]) <= SWAPS_LIMIT else "unknown"),
    }
    handlers[tag]()


def _parse_report(raw: bytes, nonce: str) -> _Report:
    """Parse one NUL-framed probe report; noise before the nonce and an unterminated tail are ignored."""
    head = nonce.encode("ascii") + b"\0"
    start = raw.find(head)
    if start < 0 or len(raw) > _REPORT_LIMIT:
        raise ProtectedTargetUnavailable("the backend returned no bounded protection report")
    fields = raw[start + len(head):].split(b"\0")
    report, index = _Report(), 0
    while index < len(fields):
        tag = _text(fields[index])
        arity = _ARITY.get(tag)
        # Every value must be NUL-terminated: the final split element is the (empty) tail.
        if arity is None or index + arity >= len(fields) - 1:
            break
        _apply(report, tag, fields[index + 1:index + 1 + arity])
        index += 1 + arity
    return report


def _prove(report: _Report, physical: str) -> RamProof:
    if report.mountinfo is None:
        return RamProof(False, "unknown", "the backend mount table is unreadable or exceeds the probe bound")
    return prove_ram_only(report.mountinfo, physical, report.swap)


def _base_refusal(report: _Report, physical: str, mode: str) -> str | None:
    if not physical.startswith("/"):
        return "not an existing directory"
    try:
        bits = int(mode, 8)
    except ValueError:
        return "its mode is unreadable"
    if bits & stat.S_IWOTH and not bits & stat.S_ISVTX:
        return "world-writable without the sticky bit"
    proof = _prove(report, physical)
    return None if proof.proven else proof.reason


Runner = Callable[..., subprocess.CompletedProcess]


def _probe(run: Runner, mode: str, args: Sequence[str]) -> _Report:
    nonce = secrets.token_hex(8)
    argv = ["sh", "-c", _PROBE, "hermes-protected-scratch", nonce, mode,
            str(MOUNTINFO_LIMIT + 1), str(SWAPS_LIMIT + 1), *args]
    try:
        done = run(argv, timeout=_PROBE_TIMEOUT)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise ProtectedTargetUnavailable(f"the protection probe could not run ({exc})") from exc
    if done.returncode != 0:
        raise ProtectedTargetUnavailable(f"the protection probe failed with exit status {done.returncode}")
    return _parse_report(done.stdout or b"", nonce)


def _directory_report(run: Runner, mode: str, root: str, children: Sequence[str]) -> ProtectedTargetDir:
    report = _probe(run, mode, [root, *children])
    if report.state != "ok":
        raise ProtectedTargetUnavailable(f"{root} is not an owner-only directory ({report.state or 'no result'})")
    paths: dict[str, str] = {}
    for child in ("", *children):
        expected = posixpath.join(root, child) if child else root
        physical, owner_mode = report.dirs.get(child, ("", ""))
        if physical != expected or owner_mode != f"{report.uid}:700":
            raise ProtectedTargetUnavailable(f"{expected} is not an owner-only 0700 directory at its own path")
        proof = _prove(report, physical)
        if not proof.proven:
            raise ProtectedTargetUnavailable(f"{expected}: {proof.reason}")
        paths[child] = physical
    try:
        identity = capture(run, [paths[child] for child in ("", *children)])
    except RuntimeChanged as exc:
        raise ProtectedTargetUnavailable(str(exc)) from exc
    return ProtectedTargetDir(root, {child: paths[child] for child in children}, _prove(report, root), identity)


def acquire(run: Runner, name: str, children: Sequence[str], *, candidates: Sequence[str] = ()) -> ProtectedTargetDir:
    """Create or reuse ``<base>/<name>`` and its children under the first proven base.

    Bases are the target's own ``$XDG_RUNTIME_DIR`` and then ``candidates``. A base must be a real
    directory, not world-writable without the sticky bit, and on ramfs, ``noswap`` tmpfs or tmpfs
    with no active swap in that target. Existing contents are reused only when every directory is
    owned by the target user, 0700 and not a link; a refused directory is retained, never deleted.
    """
    if not _NAME.fullmatch(name) or not all(_NAME.fullmatch(child) for child in children):
        raise ValueError("protected backend directory names are short lowercase [a-z0-9-] labels")
    facts = _probe(run, "facts", list(candidates))
    refusals: list[str] = []
    seen: set[str] = set()
    for requested, physical, mode in facts.candidates:
        if not requested or (physical and physical in seen):
            continue
        seen.add(physical)
        reason = _base_refusal(facts, physical, mode)
        if reason is None:
            try:
                return _directory_report(run, "provision", posixpath.join(physical, name), children)
            except ProtectedTargetUnavailable as exc:
                reason = str(exc)
        refusals.append(f"{requested}: {reason}")
    raise ProtectedTargetUnavailable("; ".join(refusals) or "no candidate directory exists")


def validate(run: Runner, root: str, children: Sequence[str], *,
             expected_identity: RuntimeIdentity | None = None) -> ProtectedTargetDir:
    """Re-prove the same existing paths and current mount/swap facts without changing them."""
    found = _directory_report(run, "verify", root, children)
    if expected_identity is not None and found.identity != expected_identity:
        raise RuntimeChanged("the original protected target generation or directory changed")
    return found


_READ_REGULAR_FILE = ('set -eu; p=$1; limit=$2; [ ! -L "$p" ] && [ -f "$p" ] || exit 98; '
                 '[ "$(stat -c %u:%h -- "$p")" = "$(id -u):1" ] || exit 98; '
                 '[ "$(stat -c %s -- "$p")" -le "$limit" ] || exit 99; '
                 'before=$(stat -c "%d:%i:%u:%h:%s:%y:%z" -- "$p"); exec 3<"$p"; '
                 '[ "$before" = "$(stat -Lc "%d:%i:%u:%h:%s:%y:%z" /proc/$$/fd/3)" ] || exit 98; '
                 'head -c "$((limit + 1))" <&3; '
                 '[ "$before" = "$(stat -Lc "%d:%i:%u:%h:%s:%y:%z" /proc/$$/fd/3)" ] || exit 98')


def regular_file_read_argv(path: str, *, max_bytes: int) -> list[str]:
    """Bounded binary read on a Linux target, pinning its owned single-link file before output.

    The caller must validate its protected runtime and capture bytes through anonymous pipes.
    This command never follows a changed pathname after its file descriptor is pinned.
    """
    if not isinstance(path, str) or not posixpath.isabs(path) or "\x00" in path:
        raise ValueError("A protected target file needs an absolute path")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise ValueError("A protected target file needs a positive read bound")
    return ["sh", "-c", _READ_REGULAR_FILE, "hermes-private-file-read", path, str(max_bytes)]
