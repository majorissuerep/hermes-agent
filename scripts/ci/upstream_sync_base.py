"""Select the reviewed upstream input during this fork's full-history integration.

The pin is NousResearch/hermes-agent main at 2026-10-14. Once fork main contains
it, ordinary PR comparisons resume. Imported authors and unchanged upstream
code have already landed upstream; scan our resolutions and counter-commits.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

UPSTREAM = "eb75627faf532fa6ffbdae59a0d14b0b385fc780"


def select(base: str, head: str, *, repo: Path | None = None) -> str:
    for ref in (base, head):
        subprocess.run(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
                       check=True, stdout=subprocess.DEVNULL, cwd=repo, timeout=120)
    if subprocess.run(["git", "cat-file", "-e", f"{UPSTREAM}^{{commit}}"], check=False,
                      stderr=subprocess.DEVNULL, cwd=repo, timeout=120).returncode:
        return base
    if (subprocess.run(["git", "merge-base", "--is-ancestor", UPSTREAM, head], check=False, cwd=repo, timeout=120).returncode == 0
            and subprocess.run(["git", "merge-base", "--is-ancestor", UPSTREAM, base], check=False, cwd=repo, timeout=120).returncode != 0):
        return UPSTREAM
    return base


if __name__ == "__main__":
    print(select(*sys.argv[1:]))
