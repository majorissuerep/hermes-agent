"""Acknowledge historical paused-backend tokens without reviving retired products."""
from __future__ import annotations

import json
from pathlib import Path
import sys


def main(context: Path, result: Path) -> int:
    # This file is a frozen path called by an already-running pre-terminal updater.
    json.loads(context.read_text(encoding="utf-8-sig"))
    result.write_text(json.dumps({"serves_handled": True}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))
