"""Ref name to version, and the next version from the published stable head.

A version is spent only by publication. Attempt refs (``rc.<N>-vX.Y.Z``) and
their abandon markers number attempts within one version; they never move the
line. CalVer tags, canary identities and receipt namespaces are not versions.
"""
from __future__ import annotations

import json
import re
from functools import cmp_to_key
from typing import Any

from hermes_cli.update_channel import STABLE_TAG_RE
from scripts.releases.semver import compare

# The legacy CalVer line published up to 0.21.5 (v2026.9.24); the pipeline continues it.
SEED = "0.21.5"
BUMPS = ("major", "minor", "patch")


def version_from_tag(ref: str) -> str | None:
    """The version a final release tag names, or None for anything else.

    ``-rc`` claims, build-metadata identities and non-``v`` receipt namespaces
    are not final tags, and a 4-digit major is a CalVer label.
    """
    if not isinstance(ref, str) or not STABLE_TAG_RE.fullmatch(ref):
        return None
    return ref[1:]


def tag_record(raw: str) -> Any:
    """Decode the JSON record an annotated release tag carries.

    ``git tag -l --format=%(contents)`` returns the whole message, and git keeps
    a signature *inside* the message body: a signed tag reads as the record
    followed by an armored signature block. The record is everything before the
    armor, so every reader parses through here, never ``json.loads(raw)``.

    Detached signatures (``git tag --detach-sign``) live outside the message and
    leave this unchanged, as does an unsigned tag.
    """
    return json.loads(raw.split("\n-----BEGIN ", 1)[0])


# The ref grammar only anchors; ``version_from_tag`` owns the version shape.
_ATTEMPT_REF_RE = re.compile(r"rc\.(?P<attempt>[1-9][0-9]*)-(?P<tag>v[^/]+)")
_MARKER_PREFIX = "abandoned-"


def parse_attempt_ref(ref: str) -> tuple[str, int] | None:
    """``rc.<N>-vX.Y.Z`` to ``(version, N)``, or None for anything else.

    The attempt comes first so the ref can never read as a SemVer prerelease
    of the version it claims.
    """
    match = _ATTEMPT_REF_RE.fullmatch(ref) if isinstance(ref, str) else None
    if match is None:
        return None
    version = version_from_tag(match.group("tag"))
    return (version, int(match.group("attempt"))) if version else None


def parse_marker_ref(ref: str) -> tuple[str, int] | None:
    """``abandoned-rc.<N>-vX.Y.Z``, the record that clears one attempt."""
    if not isinstance(ref, str) or not ref.startswith(_MARKER_PREFIX):
        return None
    return parse_attempt_ref(ref[len(_MARKER_PREFIX):])


def attempt_ref(version: str, attempt: int) -> str:
    ref = f"rc.{attempt}-v{version}"
    if parse_attempt_ref(ref) != (version, attempt):
        raise ValueError(f"invalid attempt ref: {version!r} attempt {attempt!r}")
    return ref


def marker_ref(version: str, attempt: int) -> str:
    return _MARKER_PREFIX + attempt_ref(version, attempt)


def _bump(version: str, bump: str) -> str:
    if bump not in BUMPS:
        raise ValueError(f"unknown bump {bump!r}")
    major, minor, patch = (int(part) for part in version.split("."))
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def derive_next_version(*, published: str | None, bump: str) -> str:
    """The next version from the published head, or the seed before one exists.

    Attempts do not move the line. A version is spent by publication, so an
    abandoned attempt leaves its version free for the next cut.
    """
    return _bump(published or SEED, bump)


def next_attempt(version: str, refs: list[str]) -> int:
    """One past the highest attempt ref for ``version``.

    Marker refs do not count and do not free a number: an abandoned attempt
    keeps its ref, so its number is never cut again.
    """
    attempts = [parsed[1] for parsed in map(parse_attempt_ref, refs)
                if parsed and parsed[0] == version]
    return max(attempts, default=0) + 1


def outstanding_attempts(refs: list[str], is_published) -> list[tuple[str, int, str]]:
    """The one definition of an outstanding attempt.

    An attempt ref is outstanding when its abandon marker ref does not exist
    and its version has no final tag. ``is_published(version)`` answers whether
    the final tag ``v{version}`` exists, so callers keep their own remote
    lookup. Both the release entrypoint and the sequencer refuse more than one
    of these, across all versions.
    """
    cleared = {parsed for parsed in map(parse_marker_ref, refs) if parsed}
    published: dict[str, bool] = {}
    outstanding: list[tuple[str, int, str]] = []
    for ref in refs:
        parsed = parse_attempt_ref(ref)
        if parsed is None or parsed in cleared:
            continue
        version, attempt = parsed
        if version not in published:
            published[version] = bool(is_published(version))
        if not published[version]:
            outstanding.append((version, attempt, ref))
    return outstanding
