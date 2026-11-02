"""Preserve spawning tool locations after a POSIX login profile resets PATH.

Ported from upstream 6fa171ce5423457fa46da227562ddea766c9ef69 and
35c2783b6ab7419209cad9f746f64d215f400b60. Login choices keep precedence;
relative entries are resolved by the child in its own working directory.
"""
import os
import shlex


def append_dropped_path_entries(command: str, parent_path: str) -> str:
    entries = [directory for directory in dict.fromkeys(parent_path.split(":"))
               if directory and (not os.path.isabs(directory) or os.path.isdir(directory))]
    if not entries:
        return command
    restore = (f"for __hermes_p in {' '.join(shlex.quote(directory) for directory in entries)}; do "
               'case ":$PATH:" in *":$__hermes_p:"*) ;; *) PATH="${PATH:+$PATH:}$__hermes_p" ;; esac; '
               "done; unset __hermes_p; export PATH")
    return restore + "\n" + command
