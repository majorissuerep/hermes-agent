"""Install the optional pinned Lean package through Hermes PM for native qualification."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def install() -> Path:
    """PM owns acquisition, archive integrity, the complete tree and installed-state publication."""
    import pm
    from agent.formal_logic_toolchain import installed_checker

    pm.ensure("lean", explicit=True)
    selected = installed_checker()
    if selected is None:
        raise RuntimeError("PM did not publish the pinned verified Lean package")
    return selected.executable.path


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(install())


if __name__ == "__main__":
    main()
