"""Pure producer paths: old raw Browser Use data is private and never generic cache/code."""
from pathlib import Path

LEGACY_ROOT = ('cache', 'browser-use')
LEGACY_WORKSPACE_ROOT = (*LEGACY_ROOT, 'workspace')


def legacy_workspace_root(relative):
    parts = Path(relative).parts
    if '..' in parts or len(parts) < 3 or parts[:2] != LEGACY_ROOT:
        return None
    if len(parts) >= 4 and parts[:3] == LEGACY_WORKSPACE_ROOT:
        return Path(*parts[:4])
    return Path(*LEGACY_ROOT)
    return None
