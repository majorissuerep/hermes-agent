"""Immutable Linux execution-epoch and native directory identities for outgoing callbacks.

These facts contain no browser state or credentials. They fence replacement generations and
paths, not malicious native code running as the same target user. No command provisions or
reconnects a target. The guard repeats the comparison inside the actual outgoing command.
"""
from __future__ import annotations

import posixpath
import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Sequence

MISSING_EXIT = 97
MISSING_MESSAGE = "hermes: the protected browser runtime is missing"

# Linux-only target facts, matching remote_workspace's Linux mount/swap proof domain.
# Remove through the LAST ') ' because a process comm may contain spaces or parentheses.
_FACTS = r'''
epoch() {
  b=$(cat /proc/sys/kernel/random/boot_id) || return 1
  p=$(cat /proc/1/stat) || return 1
  p=${p##*) }; set -- $p; [ "$#" -ge 20 ] || return 1
  shift 19; s=$1
  n=$(stat -Lc %d:%i /proc/self/ns/pid) || return 1
  printf '%s:%s:%s' "$b" "$s" "$n"
}
directory() {
  [ ! -L "$1" ] && [ -d "$1" ] || return 1
  [ "$(cd "$1" && pwd -P)" = "$1" ] || return 1
  [ "$(stat -c %u:%a "$1")" = "$(id -u):700" ] || return 1
  stat -c %u:%a:%d:%i "$1"
}
'''
_CAPTURE = _FACTS + r'''
set -eu
epoch; printf '\0'
for p in "$@"; do directory "$p"; printf '\0'; done
'''
_GUARD = _FACTS + r'''
refuse() { echo 'hermes: the protected browser runtime is missing or changed' >&2; exit 97; }
e=$(epoch) && [ "$e" = "$1" ] || refuse
shift
count=$1; shift
while [ "$count" -gt 0 ]; do
  actual=$(directory "$1") && [ "$actual" = "$2" ] || refuse
  shift 2; count=$((count - 1))
done
exec "$@"
'''


class RuntimeChanged(RuntimeError):
    """An original execution epoch or directory was replaced or cannot be established."""


@dataclass(frozen=True)
class RuntimeIdentity:
    epoch: str
    directories: tuple[tuple[str, str], ...]


def capture(run: Callable, paths: Sequence[str]) -> RuntimeIdentity:
    """Read bounded native identities; all directories must already be owned physical 0700 paths."""
    if not paths or len(paths) > 32 or any(not posixpath.isabs(p) or "\0" in p for p in paths):
        raise ValueError("Runtime identities require bounded absolute directory paths")
    try:
        done = run(["sh", "-c", _CAPTURE, "hermes-runtime-identity", *paths], timeout=20)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise RuntimeChanged("the original target identity is unavailable") from exc
    raw = done.stdout or b""
    fields = raw.split(b"\0")
    if done.returncode or len(raw) > 16384 or len(fields) != len(paths) + 2 or fields[-1]:
        raise RuntimeChanged("the original target returned no bounded native identity")
    try:
        values = [field.decode("ascii", "strict").strip() for field in fields[:-1]]
    except UnicodeError as exc:
        raise RuntimeChanged("the original target native identity is unreadable") from exc
    if not re.fullmatch(r"[0-9a-f-]{36}:\d+:\d+:\d+", values[0]) or any(len(v.split(":")) != 4 or not all(x.isdigit() for x in v.split(":"))
                            for v in values[1:]):
        raise RuntimeChanged("the original target native identity is unreadable")
    return RuntimeIdentity(values[0], tuple(zip(paths, values[1:])))


def guarded_argv(identity: RuntimeIdentity, argv: Sequence[str]) -> list[str]:
    """Fence replacement after host-side validation, before executing argv on the selected target."""
    if not isinstance(identity, RuntimeIdentity) or not identity.directories or not argv:
        raise RuntimeChanged("the original protected runtime identity was not captured")
    pairs = [value for pair in identity.directories for value in pair]
    return ["sh", "-c", _GUARD, "hermes-browser-guard", identity.epoch,
            str(len(identity.directories)), *pairs, *argv]
