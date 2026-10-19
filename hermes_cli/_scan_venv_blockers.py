"""Compatibility entry point for historical Desktop venv preflights.

PM stages fresh runtime generations instead of modifying a live venv. Current
Desktop no longer calls this module, but older binaries can load it from an
updated checkout. Keep their JSON protocol without scanning or stopping anything.
"""

from __future__ import annotations

import json
import sys


def _is_pausable_gateway(cmdline: str) -> bool:
    """Retired hosts are never candidates for legacy pause/termination."""
    return False


def main() -> None:
    """Emit one JSON document for old Desktop callers, without process access."""
    if sys.argv[1:2] == ["--terminate-safe"]:
        # Old Desktop treats exit 0 as a successful stop without reading stdout.
        # A stale preview request must never claim to have stopped a process.
        print(json.dumps({
            "ok": False,
            "error": "Legacy preview termination is retired; no processes were stopped.",
        }))
        raise SystemExit(1)
    print(json.dumps({
        "ok": True,
        "blocked": False,
        "processes": [],
        "retired": True,
        "message": "Legacy venv preflight is retired; PM stages fresh runtime generations.",
    }))
    raise SystemExit(0)


if __name__ == "__main__":
    main()
