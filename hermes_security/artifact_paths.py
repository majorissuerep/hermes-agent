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
_SNAPSHOT_CACHE_ROOTS = (("cache", "web"), ("web_cache",))
PRIVATE_CACHE_ROOTS = (*_OUTPUT_CACHE_ROOTS, *_DELEGATION_ROOTS)
LEGACY_ROOTS = (*PRIVATE_CACHE_ROOTS, ("browser_screenshots",))
ARTIFACT_ROOTS = (("artifacts", "terminal-output"), ("artifacts", "tool-results"),
                  ("artifacts", "browser"), ("artifacts", "browser-snapshots"),
                  ("artifacts", "generated", "images"), ("artifacts", "generated", "videos"),
                  ("artifacts", "media", "images"),
                  ("artifacts", "media", "audio"), ("artifacts", "media", "documents"),
                  *_OUTPUT_CACHE_ROOTS, ("browser_screenshots",), *_DELEGATION_ROOTS)
IMAGE_ROOTS = (("artifacts", "browser"), ("artifacts", "media", "images"),
               ("cache", "screenshots"), ("browser_screenshots",))
_TERMINAL_ROOTS = (("artifacts", "terminal-output"), ("cache", "terminal-output"))
_LEGACY_MEDIA = {("cache", "images"): ("images", "img"), ("image_cache",): ("images", "img"),
                 ("cache", "audio"): ("audio", "audio"), ("audio_cache",): ("audio", "audio"),
                 ("cache", "documents"): ("documents", "doc"), ("document_cache",): ("documents", "doc")}
# computer_use's own retained outputs from older builds, by exact producer name only: other producers'
# files in these shared caches stay outside private policy.
_CUA_SHOT, _CUA_TREE = r"computer_use_[a-f0-9]{32}\.(?:png|jpe?g|webp)", r"elements_[a-f0-9]{32}\.json"
_LEGACY_COMPUTER_USE = {("cache", "images"): ("images", _CUA_SHOT), ("image_cache",): ("images", _CUA_SHOT),
                        ("cache", "computer_use"): ("documents", _CUA_TREE),
                        ("computer_use_cache",): ("documents", _CUA_TREE)}


def _under(relative, roots) -> bool:
    parts = Path(relative).parts
    return ".." not in parts and any(len(parts) > len(root) and parts[:len(root)] == root
                                     for root in roots)


def is_artifact_relative_path(relative) -> bool:
    if is_legacy_browser_snapshot_path(relative) or is_private_pet_state(relative) or media_kind(relative):
        return True
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
    if parts[:3] in {("artifacts", "generated", "pet-store"), ("artifacts", "generated", "pet-drafts")}:
        return len(parts) == 3 or (len(parts) == 4 and re.fullmatch(r"[0-9a-f]{12}|[0-9a-f]{32}", parts[3]) is not None)
    delegated = _delegation_parts(relative)
    if delegated is not None:
        return delegated == () or (len(delegated) in (1, 2) and delegated[0] == "live")
    return parts in ARTIFACT_ROOTS or _under(relative, ARTIFACT_ROOTS)


def is_image_relative_path(relative) -> bool:
    return _under(relative, IMAGE_ROOTS) or media_kind(relative) == "images"


def media_kind(relative) -> str | None:
    generated = generated_kind(relative)
    if generated:
        return generated
    parts = Path(relative).parts
    if ".." in parts:
        return None
    if len(parts) == 4 and parts[:2] == ("artifacts", "media"):
        return parts[2] if parts[2] in {"images", "audio", "documents"} else None
    if _legacy_tui_image(relative):
        return "images"
    family = _LEGACY_MEDIA.get(parts[:-1])
    if family and re.fullmatch(family[1] + r"_[a-f0-9]{32}\.[A-Za-z0-9]{1,12}", parts[-1]):
        return family[0]
    producer = _LEGACY_COMPUTER_USE.get(parts[:-1])
    return producer[0] if producer and re.fullmatch(producer[1], parts[-1]) else None


def _legacy_tui_image(relative) -> bool:
    """Published uploads, clipboard captures and PDF pages in the private images root.

    These files already used path-bound state envelopes during home migration.
    Admit their historical names without turning arbitrary profile state into media.
    """
    parts = Path(relative).parts
    return (len(parts) == 2 and parts[0] == "images" and re.fullmatch(
        r"(?:upload_[a-z0-9_]{8}|pdf_p[0-9]+_[a-z0-9_]{8}|"
        r"clip_(?:[a-z0-9_]{8}|[0-9]{8}_[0-9]{6}_[0-9]+))"
        r"\.(?:png|jpe?g|gif|webp|bmp|tiff?|avif|heic|heif|svg|ico)",
        parts[-1], flags=re.IGNORECASE) is not None)


def generated_kind(relative) -> str | None:
    """Provider deliverables, including the precise historical only-copy family."""
    path = Path(relative)
    parts = path.parts
    if is_private_pet_state(path) and path.name != "registry.json":
        return "images"
    if (len(parts) == 5 and parts[:3] == ("artifacts", "generated", "pet-drafts")
            and _pet_draft_parts(parts[3:])):
        return "images"
    if len(parts) == 4 and parts[:2] == ("cache", "pet-gen") and _pet_draft_parts(parts[2:]):
        return "images"
    if ".." in parts or len(parts) != 4 or parts[2] not in {"images", "videos"}:
        return None
    if parts[:2] == ("artifacts", "generated"):
        return parts[2]
    if (parts[:2] == ("cache", "generated")
            and re.fullmatch(r"[A-Za-z0-9_.-]+_\d{8}_\d{6}_[0-9a-f]{8}\.[A-Za-z0-9]{1,12}", path.name)):
        return parts[2]
    return None


def _pet_draft_parts(parts) -> bool:
    return (re.fullmatch(r"[0-9a-f]{12}", parts[0]) is not None
            and re.fullmatch(r"draft-\d+\.png|reference\.(?:png|jpg|jpeg|webp|gif)", parts[1]) is not None)


def is_private_pet_state(relative) -> bool:
    parts = Path(relative).parts
    if parts == ("artifacts", "generated", "pet-store", "registry.json"):
        return True
    return (len(parts) == 5 and parts[:3] == ("artifacts", "generated", "pet-store")
            and re.fullmatch(r"[0-9a-f]{32}", parts[3]) is not None
            and parts[4] in {"spritesheet.png", "spritesheet.webp", "thumbnail.png"})


def legacy_media_codec(relative) -> str | None:
    """Published document_cache was migrated, unlike the excluded cache/* roots.

    Keep its historical suffix codecs for offline repair/export. They are not
    admitted by the runtime artifact reader: old frames have no per-path AAD.
    """
    path = Path(relative)
    if path.parts[:-1] != ("document_cache",) or media_kind(path) != "documents":
        return None
    return {".db": "database", ".sqlite": "database", ".sqlite3": "database",
            ".log": "log", ".jsonl": "transcript", ".env": "env"}.get(path.suffix)


def terminal_frame_purpose(relative) -> str | None:
    """Bind terminal frames' KDF and authenticated data to their exact owner-relative path."""
    return "artifact-terminal:v1:" + Path(relative).as_posix() if _under(relative, _TERMINAL_ROOTS) else None


def is_legacy_artifact_path(relative) -> bool:
    return (_under(relative, LEGACY_ROOTS) or is_legacy_browser_snapshot_path(relative)
            or (media_kind(relative) is not None and Path(relative).parts[0] != "artifacts"))


def is_legacy_browser_snapshot_path(relative) -> bool:
    """Only the browser-owned text family in an otherwise public web cache."""
    parts = Path(relative).parts
    return (parts[:-1] in _SNAPSHOT_CACHE_ROOTS
            and re.fullmatch(r"browser-snapshot-[0-9a-f]{10}\.txt", parts[-1]) is not None)


def is_private_cache_branch(relative) -> bool:
    """Admit only old private cache roots and their ancestors while walking."""
    parts = Path(relative).parts
    return (parts == ("cache",) or _under(relative, PRIVATE_CACHE_ROOTS) or parts in PRIVATE_CACHE_ROOTS
            or parts in _SNAPSHOT_CACHE_ROOTS or is_legacy_browser_snapshot_path(relative)
            or parts in _LEGACY_MEDIA or parts in _LEGACY_COMPUTER_USE
            or parts in {("cache", "generated"), ("cache", "generated", "images"),
                                                 ("cache", "generated", "videos")}
            or parts == ("cache", "pet-gen")
            or (len(parts) == 3 and parts[:2] == ("cache", "pet-gen")
                and re.fullmatch(r"[0-9a-f]{12}", parts[2]) is not None)
            or media_kind(relative) is not None)
