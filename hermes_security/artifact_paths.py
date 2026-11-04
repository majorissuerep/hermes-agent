"""Reserved private outputs, including the cache families written by older builds.

Package-manager and other public caches remain excluded from state migration.
These predicates inspect relative paths only; the artifact reader authenticates
the owning home and pins directory descriptors before opening any bytes.
"""
from __future__ import annotations

from pathlib import Path
import re

_OUTPUT_CACHE_ROOTS = (("cache", "terminal-output"), ("cache", "spillover"), ("cache", "screenshots"))
_DELEGATION_ROOT = ("cache", "delegation")
_DELEGATION_ROOTS = (_DELEGATION_ROOT, ("delegation_cache",))
PRIVATE_CACHE_ROOTS = (*_OUTPUT_CACHE_ROOTS, *_DELEGATION_ROOTS)
LEGACY_ROOTS = (*PRIVATE_CACHE_ROOTS, ("browser_screenshots",))
ARTIFACT_ROOTS = (("artifacts", "terminal-output"), ("artifacts", "tool-results"),
                  ("artifacts", "browser"), *_OUTPUT_CACHE_ROOTS, ("browser_screenshots",), *_DELEGATION_ROOTS)
IMAGE_ROOTS = (("artifacts", "browser"), ("cache", "screenshots"), ("browser_screenshots",))
_TERMINAL_ROOTS = (("artifacts", "terminal-output"), ("cache", "terminal-output"))


def _under(relative, roots) -> bool:
    parts = Path(relative).parts
    return ".." not in parts and any(len(parts) > len(root) and parts[:len(root)] == root
                                     for root in roots)


def is_artifact_relative_path(relative) -> bool:
    if _under(relative, _DELEGATION_ROOTS):
        return is_delegation_output_path(relative)
    return _under(relative, ARTIFACT_ROOTS)


def _delegation_parts(relative):
    parts = Path(relative).parts
    if ".." not in parts:
        for root in _DELEGATION_ROOTS:
            if parts[:len(root)] == root:
                return parts[len(root):]
    return None


def is_delegation_log_path(relative) -> bool:
    parts = _delegation_parts(relative)
    return (parts is not None and len(parts) == 3 and parts[0] == "live"
            and re.fullmatch(r"task-\d+\.log", parts[-1]) is not None)


def is_delegation_manifest_path(relative) -> bool:
    parts = _delegation_parts(relative)
    return (parts is not None and len(parts) == 3 and parts[0] == "live"
            and parts[-1] == "manifest.json")


def is_delegation_output_path(relative) -> bool:
    parts = _delegation_parts(relative)
    summary = (parts is not None and len(parts) == 1
               and re.fullmatch(r"subagent-summary--?\d+-\d{8}_\d{6}_\d{6}\.txt", parts[-1]) is not None)
    return summary or is_delegation_log_path(relative)


def is_artifact_directory(relative) -> bool:
    parts = Path(relative).parts
    if ".." in parts:
        return False
    delegated = _delegation_parts(relative)
    if delegated is not None:
        return delegated == () or (len(delegated) in (1, 2) and delegated[0] == "live")
    return parts in ARTIFACT_ROOTS or _under(relative, ARTIFACT_ROOTS)


def is_image_relative_path(relative) -> bool:
    return _under(relative, IMAGE_ROOTS)


def terminal_frame_purpose(relative) -> str | None:
    """Bind terminal frames' KDF and authenticated data to their exact owner-relative path."""
    return "artifact-terminal:v1:" + Path(relative).as_posix() if _under(relative, _TERMINAL_ROOTS) else None


def is_legacy_artifact_path(relative) -> bool:
    return _under(relative, LEGACY_ROOTS)


def is_private_cache_branch(relative) -> bool:
    """Admit only old private cache roots and their ancestors while walking."""
    parts = Path(relative).parts
    return parts == ("cache",) or _under(relative, PRIVATE_CACHE_ROOTS) or parts in PRIVATE_CACHE_ROOTS
