"""Projects (``projects.*`` — per-profile multi-folder workspaces, repo discovery, sidebar tree)
and the pet mascot surface (``pet.*`` — gallery, sprite payloads, adopt/remove/rename).

Every method here is profile-scoped: the desktop's ``projectParams`` / ``petRpc`` wrappers add
``profile`` so app-global remote mode reads the focused profile's ``projects.db`` / ``config.yaml``.
The pet wire predates the snake_case rule and travels camelCase (``displayName``,
``spritesheetBase64``); the field names below are those wire keys verbatim.
"""

from __future__ import annotations

from pydantic import Field

from .base import JsonValue, Params, Result
from .common import OkResult, OpenModel, ProfileParams, StoredSessionRow
from .registry import method

# ── pet: active mascot ────────────────────────────────────────────────────────────────────────


class PetInfoParams(ProfileParams):
    """``knownRevision``: the spritesheet revision the caller already holds (send-once bytes)."""

    knownRevision: str | None = None


class PetInfoResult(OpenModel):
    """``server._pet_sprite_payload`` behind ``enabled``; every sprite field is absent when the pet
    display is off, ``spritesheetBase64`` is elided when ``spritesheetUnchanged``."""

    enabled: bool
    slug: str | None = None
    displayName: str | None = None
    mime: str | None = None
    spritesheetBase64: str | None = None
    spritesheetRevision: str | None = None
    spritesheetUnchanged: bool | None = None
    frameW: int | None = None
    frameH: int | None = None
    framesPerState: int | None = None
    framesByState: dict[str, int] | None = None
    framesByRow: dict[str, int] | None = None
    loopMs: int | None = None
    scale: float | None = None
    stateRows: list[str] | None = None


method("pet.info", params=PetInfoParams, result=PetInfoResult,
       doc="Active pet for sprite renderers: spritesheet (base64) + frame geometry + state-row taxonomy.")


class PetInfoMetaResult(Result):
    enabled: bool
    slug: str | None = None
    displayName: str | None = None
    scale: float | None = None
    spritesheetRevision: str | None = None


method("pet.info.meta", params=ProfileParams, result=PetInfoMetaResult,
       doc="Cheap active-pet metadata used to avoid full payload refreshes.")


class PetCellsParams(ProfileParams):
    """``graphics`` opts into the kitty payload when the TTY speaks it; ``cols`` overrides the width."""

    state: str | None = None
    cols: int | None = None
    graphics: bool = False


class PetCellsResult(Result):
    """Unicode: ``frames`` is frame → row → cell ``[tr,tg,tb,ta, br,bg,bb,ba]``; kitty (``graphics``
    set): ``frames`` are transmit escapes and ``placeholder`` the text grid."""

    enabled: bool
    slug: str | None = None
    displayName: str | None = None
    state: str | None = None
    cols: int | None = None
    frameMs: float | None = None
    frames: list[list[list[list[int]]]] | list[str] | None = None
    scale: float | None = None
    graphics: str | None = None
    imageId: int | None = None
    color: str | None = None
    rows: int | None = None
    placeholder: list[str] | None = None


method("pet.cells", params=PetCellsParams, result=PetCellsResult,
       doc="Half-block cell frames (or a kitty placement) for one pet state.")


# ── pet: gallery / picker ─────────────────────────────────────────────────────────────────────


class PetGalleryParams(ProfileParams):
    localOnly: bool = False


class PetGalleryEntry(Result):
    slug: str
    displayName: str
    installed: bool
    spritesheetUrl: str = ""
    curated: bool | None = None
    generated: bool = False


class PetGalleryResult(Result):
    enabled: bool
    active: str = ""
    pets: list[PetGalleryEntry] = Field(default_factory=list)


method("pet.gallery", params=PetGalleryParams, result=PetGalleryResult,
       doc="Petdex gallery + local install state (installed-only offline); localOnly skips the remote manifest.")


class PetSlugParams(ProfileParams):
    slug: str


class PetSlugResult(Result):
    ok: bool
    slug: str
    displayName: str | None = None


method("pet.select", params=PetSlugParams, result=PetSlugResult,
       doc="Adopt a pet: install (if needed) + activate; writes display.pet.* to config.")
method("pet.remove", params=PetSlugParams, result=PetSlugResult,
       doc="Uninstall a pet (delete its directory); if it was active, turn the display off.")


class PetRenameParams(PetSlugParams):
    name: str


method("pet.rename", params=PetRenameParams, result=PetSlugResult,
       doc="Rename a pet's display name + realign its slug/dir; follows the active slug in config.")


class PetExportResult(Result):
    ok: bool
    filename: str
    zipBase64: str


method("pet.export", params=PetSlugParams, result=PetExportResult,
       doc="Export an installed pet as a re-importable .zip.")


class PetThumbParams(PetSlugParams):
    """``url``: spritesheet source for a not-yet-installed pet."""

    url: str | None = None


class PetThumbResult(Result):
    ok: bool
    slug: str
    dataUri: str | None = None


method("pet.thumb", params=PetThumbParams, result=PetThumbResult,
       doc="Idle-frame PNG data URI for the picker (desktop CSP breaks CDN <img>).")

method("pet.disable", params=ProfileParams, result=OkResult,
       doc="Turn the pet display off from the desktop picker.")


class PetScaleParams(ProfileParams):
    scale: JsonValue = None  # number or numeric string; a non-number answers 4004


class PetScaleResult(Result):
    ok: bool
    scale: float


method("pet.scale", params=PetScaleParams, result=PetScaleResult,
       doc="Persist display.pet.scale (clamped to engine bounds) from the desktop slider.")
