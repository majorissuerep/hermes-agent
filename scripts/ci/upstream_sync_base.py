"""Select the reviewed upstream input during this fork's full-history integration.

The pin is NousResearch/hermes-agent main at 2026-10-03. Once fork main contains
it, ordinary PR comparisons resume. Imported authors and unchanged upstream
code have already landed upstream; scan our resolutions and counter-commits.
"""

from __future__ import annotations

import subprocess
import sys

UPSTREAM = "3c9847f5e86e81c23335a54daa532de28eeffeb3"


def select(base: str, head: str) -> str:
    for ref in (base, head):
        subprocess.run(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
                       check=True, stdout=subprocess.DEVNULL)
    if subprocess.run(["git", "cat-file", "-e", f"{UPSTREAM}^{{commit}}"],
                      stderr=subprocess.DEVNULL).returncode:
        return base
    if (subprocess.run(["git", "merge-base", "--is-ancestor", UPSTREAM, head]).returncode == 0
            and subprocess.run(["git", "merge-base", "--is-ancestor", UPSTREAM, base]).returncode != 0):
        return UPSTREAM
    return base


if __name__ == "__main__":
    print(select(*sys.argv[1:]))
