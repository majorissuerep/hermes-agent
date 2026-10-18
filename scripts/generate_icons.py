#!/usr/bin/env python3
"""Generate committed website branding from the Nous vector artwork."""

from __future__ import annotations

import argparse
import io
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

try:
    import resvg_py
except ImportError:
    sys.exit(
        "resvg-py is missing: run the generator with a Hermes runtime interpreter\n"
        "  (HERMES_PYTHON=<hermes venv python> node scripts/generate-icons.mjs)"
    )

# The nous dark background (#0d1117) — fixed dark tile/background everywhere.
DARK_HEX = "#0d1117"
DARK_RGB = (13, 17, 23)
BORDER_FRACTION = 0.0407747197
# The brand ring is switched off everywhere (tiles and the macOS 26 layers):
# flat plate + girl read better beside macOS 26's own icons. The ring code
# stays so it can be turned back on in one place.
BORDER_ENABLED = False
# Without a ring the girl's lowest nodes are dragged past the tile edge (in
# 1024ths of the tile) so the outline crops her instead of leaving a gap.
EDGE_OVERSHOOT = 48 / 1024

# Portrait boxes fitted to the reference at equal visible tile width, with
# uniform scaling about the tile center followed by an up-left translation.
# Keep their y coordinate: bottom anchoring would undo the registration.
GIRL_BOXES = {
    "squircle-light.svg": (72.149433, 104.703674, 872.767801, 872.767801),
    "squircle-dark.svg": (72.149433, 104.703674, 872.767801, 872.767801),
    # Mac: the girl scaled 1.06x about the plate center; the plate stays on the
    # 824 grid, but a white tile with a ring reads small beside full-color peers.
    "squircle-mac-light.svg": (140.19, 164.44, 744.68, 744.68),
    "squircle-mac-dark.svg": (140.19, 164.44, 744.68, 744.68),
}
# The macOS 26 layered icon's canvas is the plate itself (the system maps it
# onto the 824 grid), so the same mac portrait lands in plate coordinates.
_mx, _my, _mw, _mh = GIRL_BOXES["squircle-mac-light.svg"]
_plate = 1024.0 / 824.0
GIRL_BOXES["icon.icon"] = ((_mx - 100.0) * _plate, (_my - 100.0) * _plate, _mw * _plate, _mh * _plate)
# The brand-kit SVG canvas (both girl svgs share this viewBox).
GIRL_VIEWBOX = 5487.0615

# Target sizes for --check's structural verification: relpath -> (format, size)
CHECK_SIZES: dict[str, tuple[str, tuple[int, int]]] = {
    "website/static/img/logo.png": ("PNG", (1772, 1799)),
    "website/static/img/logo-dark.png": ("PNG", (1772, 1799)),
    "website/static/img/nous-logo.png": ("PNG", (150, 150)),
    "website/static/img/nous-logo-dark.png": ("PNG", (150, 150)),
    "website/static/img/favicon-16x16.png": ("PNG", (16, 16)),
    "website/static/img/favicon-32x32.png": ("PNG", (32, 32)),
    "website/static/img/apple-touch-icon.png": ("PNG", (180, 180)),
}

# (relpath, kind, arg)
TARGETS: list[tuple[str, str, object]] = [
    ("assets/icon-master.svg", "svg", None),
    ("assets/icon-master-dark.svg", "svg_dark", None),
    ("website/static/img/logo.png", "logo", None),
    ("website/static/img/logo-dark.png", "logo_dark", None),
    ("website/static/img/nous-logo.png", "png_white", 150),
    ("website/static/img/nous-logo-dark.png", "png_dark_white", 150),
    ("website/static/img/favicon-16x16.png", "png", 16),
    ("website/static/img/favicon-32x32.png", "png", 32),
    ("website/static/img/apple-touch-icon.png", "png", 180),
    ("website/static/img/favicon.ico", "ico", [16, 32, 48]),
    ("website/static/img/favicon.svg", "svg_copy", None),
]

# ─── girl art extraction ────────────────────────────────────────────────────

class IconArt:
    """One generation's rendering inputs and caches; never writes to source."""

    def __init__(self, source: Path, *, colors: tuple[str, str] | None = None, commit: str = ""):
        assets = source / "assets"
        self.source = source
        self.colors = colors
        self.commit = commit
        self.girls = {color: assets / f"nous-girl-{color}.svg" for color in ("black", "white")}
        self.backgrounds = assets / "backgrounds"
        self.paths: dict[str, str] = {}
        self.bboxes: dict[str, tuple[float, float, float, float]] = {}
        self.master = compose_svg(self, "black", "squircle-light.svg")
        self.master_dark = compose_svg(self, "white", "squircle-dark.svg")
        # Badge-free twins for direct renders below BADGE_MIN_SIZE.
        self.master_small = compose_svg(self, "black", "squircle-light.svg", badge=False)
        self.master_dark_small = compose_svg(self, "white", "squircle-dark.svg", badge=False)


def girl_path(art: IconArt, girl: str) -> str:
    """The girl `<path>` element with editor metadata stripped (resvg rejects
    undeclared inkscape/sodipodi prefixes)."""
    if girl not in art.paths:
        src = art.girls[girl].read_text(encoding="utf-8-sig")
        m = re.search(r"<path\b.*?/>", src, re.DOTALL)
        assert m, f"no <path> found in {art.girls[girl].name}"
        path = re.sub(r'\s+(inkscape|sodipodi):[a-zA-Z-]+="[^"]*"', "", m.group(0))
        art.paths[girl] = path
    return art.paths[girl]


def girl_bbox(art: IconArt, girl: str) -> tuple[float, float, float, float]:
    """Art bounding box in the girl SVG's coordinate space, measured by
    rendering once and taking the alpha bbox (robust to art changes)."""
    if girl not in art.bboxes:
        data = resvg_py.svg_to_bytes(svg_path=str(art.girls[girl]), width=512, height=512)
        im = Image.open(io.BytesIO(data))
        bx, by, bx2, by2 = im.getchannel("A").point(lambda v: 255 if v > 0 else 0).getbbox()
        s = GIRL_VIEWBOX / 512.0
        art.bboxes[girl] = (bx * s, by * s, (bx2 - bx) * s, (by2 - by) * s)
    return art.bboxes[girl]


def girl_layer(
    art: IconArt, girl: str, box: tuple[float, float, float, float],
    *, align: str = "xMidYMid",
) -> str:
    """Nested-svg layer: girl art (bbox as viewBox) placed into `box` — the
    box's aspect is preserved via 'meet', so the girl never distorts."""
    bx, by, bw, bh = girl_bbox(art, girl)
    x, y, w, h = box
    return (
        f'<svg x="{x}" y="{y}" width="{w}" height="{h}" viewBox="{bx} {by} {bw} {bh}" '
        f'preserveAspectRatio="{align} meet">\n'
        f"    {girl_path(art, girl)}\n"
        "  </svg>"
    )


def background_inner(art: IconArt, name: str) -> tuple[str, int, int]:
    """Inner content + (width, height) of a background SVG asset."""
    text = (art.backgrounds / name).read_text(encoding="utf-8-sig")
    if art.colors:
        text = text.replace('fill="#ffffff"', f'fill="{art.colors[0]}"')
        text = text.replace(f'fill="{DARK_HEX}"', f'fill="{art.colors[1]}"')
    root = ET.fromstring(text)
    m = re.fullmatch(r"0 0 (\d+(?:\.\d+)?) (\d+(?:\.\d+)?)", root.get("viewBox", ""))
    assert m, f"cannot parse viewBox of {name}"
    w, h = float(m.group(1)), float(m.group(2))
    # Editor exports include XML declarations and root-scoped namespaces.
    # Parse away the prolog and retain child namespaces when embedding.
    inner = "".join(ET.tostring(child, encoding="unicode") for child in root)
    return inner, int(w), int(h)


# Five-by-seven lowercase hexadecimal glyphs, one five-bit row at a time.
# Vector cells keep release builds deterministic without any installed fonts.
HEX_GLYPHS = {
    "0": (14, 17, 19, 21, 25, 17, 14),
    "1": (4, 12, 4, 4, 4, 4, 14),
    "2": (14, 17, 1, 2, 4, 8, 31),
    "3": (30, 1, 1, 14, 1, 1, 30),
    "4": (2, 6, 10, 18, 31, 2, 2),
    "5": (31, 16, 16, 30, 1, 1, 30),
    "6": (14, 16, 16, 30, 17, 17, 14),
    "7": (31, 1, 2, 4, 8, 8, 8),
    "8": (14, 17, 17, 14, 17, 17, 14),
    "9": (14, 17, 17, 15, 1, 1, 14),
    "a": (0, 0, 14, 1, 15, 17, 15),
    "b": (16, 16, 30, 17, 17, 17, 30),
    "c": (0, 0, 14, 16, 16, 17, 14),
    "d": (1, 1, 15, 17, 17, 17, 15),
    "e": (0, 0, 14, 17, 31, 16, 14),
    "f": (6, 9, 8, 28, 8, 8, 8),
}


# Commit badge plate and glyph origin on the 1024 canvas.
BADGE_RECT = (160, 28, 704, 152)
BADGE_GLYPH_ORIGIN = (184, 48)
# Below this edge length a direct render carries no badge: the plate's top
# row is then under one device pixel from the badge, whose anti-aliased edge
# would stack coverage on the tile's own corner pixels and change the alpha
# channel between flavors. Nobody reads a SHA at 16px anyway.
BADGE_MIN_SIZE = 32


def commit_layer(commit: str, bg: str) -> str:
    cells = []
    gx, gy = BADGE_GLYPH_ORIGIN
    for index, char in enumerate(commit[:7]):
        for row, bits in enumerate(HEX_GLYPHS[char]):
            for col in range(5):
                if bits & (1 << (4 - col)):
                    x, y = gx + (index * 6 + col) * 16, gy + row * 16
                    cells.append(f"M{x} {y}h16v16h-16z")
    # The badge follows the tile's mac HIG inset, never the outer canvas.
    transform = f' transform="translate(100 100) scale({824 / 1024})"' if "-mac-" in bg else ""
    bx, by, bw, bh = BADGE_RECT
    return (
        f'<g{transform}><rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="24" fill="#000000"/>'
        f'<path fill="#ffffff" d="{"".join(cells)}"/></g>'
    )


def drag_bottom_nodes(path: ET.Element, *, cutoff: float, band: float, distance: float) -> None:
    """Drag lower nodes and curve handles, tapering to zero above the bottom band."""
    y_scale, y_offset = 1.0, 0.0
    transform = path.get("transform")
    if transform:
        matrix = re.fullmatch(r"matrix\(([^)]+)\)", transform)
        if matrix is None:
            raise ValueError("bottom node edits require an axis-aligned matrix")
        _, b, c, y_scale, _, y_offset = map(float, re.split(r"[\s,]+", matrix[1].strip()))
        if b != 0 or c != 0 or y_scale <= 0:
            raise ValueError("bottom node edits require an upright axis-aligned matrix")

    # The brand exports use explicit absolute M/L/C commands. Reject other
    # commands rather than silently corrupting relative coordinates or arcs.
    tokens = re.findall(r"[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?", path.attrib["d"])
    counts = {"M": 2, "L": 2, "C": 6, "z": 0, "Z": 0}
    index = 0
    while index < len(tokens):
        command = tokens[index]
        if command not in counts:
            raise ValueError("bottom node edits require explicit absolute M/L/C commands")
        count = counts[command]
        if index + count >= len(tokens):
            raise ValueError("incomplete SVG path command")
        for offset in range(2, count + 1, 2):
            token_index = index + offset
            raw_y = float(tokens[token_index])
            amount = min(1.0, max(0.0, (raw_y * y_scale + y_offset - cutoff) / band))
            if amount:
                weight = amount * amount * (3 - 2 * amount)
                tokens[token_index] = f"{raw_y + distance * weight / y_scale:.12g}"
        index += count + 1
    path.set("d", " ".join(tokens))


def portrait_layer(art: IconArt, girl: str, bg: str, join_bottom: float) -> ET.Element:
    """The registered girl with her lowest nodes dragged down to `join_bottom`
    (the border's inner edge) so hair meets the ring instead of floating."""
    box = GIRL_BOXES[bg]
    portrait = ET.fromstring(girl_layer(art, girl, box, align="xMidYMax"))
    _, y, portrait_width, portrait_height = box
    _, by, bw, bh = girl_bbox(art, girl)
    scale = min(portrait_width / bw, portrait_height / bh)
    drag_bottom_nodes(
        portrait[0], cutoff=by + bh * 0.97, band=bh * 0.02,
        distance=max(0.0, join_bottom - (y + portrait_height)) / scale,
    )
    # Keep the fitted viewBox fixed, but let edited nodes reach into the border.
    portrait.set("overflow", "visible")
    return portrait


def compose_svg(art: IconArt, girl: str, bg: str, *, badge: bool = True) -> str:
    """Full svg text: background + girl layer, in the background's native
    coordinate space (resvg scales to whatever output size is requested, so
    the composition is size-agnostic — no manual box scaling)."""
    inner, w, h = background_inner(art, bg)
    background = ET.fromstring(f"<g>{inner}</g>")
    tile = background.find("{http://www.w3.org/2000/svg}rect")
    assert tile is not None, f"no background rectangle in {bg}"
    geometry = {key: float(tile.attrib[key]) for key in ("x", "y", "width", "height", "rx")}
    silhouette = ET.Element("rect", {key: str(value) for key, value in geometry.items()})
    bottom = geometry["y"] + geometry["height"]
    if BORDER_ENABLED:
        thickness = geometry["width"] * BORDER_FRACTION
        # An inward stroke keeps the outer platform geometry unchanged. Subtracting
        # the same inset from rx (not scaling rx) keeps the corner thickness uniform.
        inset = {"x": 1, "y": 1, "width": -2, "height": -2, "rx": -1}
        for key, value in geometry.items():
            tile.set(key, str(value + inset[key] * thickness / 2))
        tile.set("stroke", "#000000" if girl == "black" else "#ffffff")
        tile.set("stroke-width", str(thickness))
        join_bottom = bottom - thickness + 10
    else:
        join_bottom = bottom + geometry["width"] * EDGE_OVERSHOOT
    inner = "".join(ET.tostring(child, encoding="unicode") for child in background)
    clip = ET.tostring(silhouette, encoding="unicode")
    portrait = portrait_layer(art, girl, bg, join_bottom)
    # The badge shares the portrait's clip so it can never paint past the plate.
    badge_svg = commit_layer(art.commit, bg) if art.commit and badge else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}">\n'
        f'  <defs><clipPath id="icon-silhouette">{clip}</clipPath></defs>\n'
        f"  {inner.strip()}\n"
        f'  <g clip-path="url(#icon-silhouette)">{badge_svg}{ET.tostring(portrait, encoding="unicode")}</g>\n'
        "</svg>\n"
    )


# ─── rendering ──────────────────────────────────────────────────────────────

def render(master: str, size: int, *, background: str | None = None) -> Image.Image:
    """Render a master to an RGBA PNG of `size`x`size`."""
    data = resvg_py.svg_to_bytes(
        svg_string=master, width=size, height=size, background=background
    )
    return Image.open(io.BytesIO(data)).convert("RGBA")


def render_svg(svg: str, size: int | tuple[int, int]) -> Image.Image:
    if isinstance(size, int):
        w = h = size
    else:
        w, h = size
    data = resvg_py.svg_to_bytes(svg_string=svg, width=w, height=h)
    return Image.open(io.BytesIO(data)).convert("RGBA")


def paste_centered(canvas: Image.Image, img: Image.Image) -> None:
    x = (canvas.width - img.width) // 2
    y = (canvas.height - img.height) // 2
    canvas.alpha_composite(img, (x, y))


def save_png(img: Image.Image, buf: io.BytesIO) -> None:
    """Save with alpha preserved. Flat art quantizes losslessly to an 8-bit
    palette (tRNS per-index alpha keeps the AA edges), so use that when the
    palette round-trips pixel-identically; fall back to RGBA otherwise."""
    if img.mode != "RGBA":
        img.convert("RGBA").save(buf, "PNG", optimize=True)
        return
    quantized = img.quantize(colors=256, method=Image.FASTOCTREE, dither=Image.NONE)
    if quantized.convert("RGBA").tobytes() == img.tobytes():
        quantized.save(buf, "PNG", optimize=True)
    else:
        img.save(buf, "PNG", optimize=True)


def build_logo_image(art: IconArt, dark: bool = False) -> Image.Image:
    """1772x1799 wordmark: the girl alone on transparency (no frame), centered.
    Light = black girl, dark = white girl — the consuming surface's background
    (navbar light/dark) shows through."""
    W, H = 1772, 1799
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">\n  {girl_layer(art, "white" if dark else "black", (0, 0, W, H))}\n</svg>\n'
    return render_svg(svg, (W, H))


def target_bytes(art: IconArt, kind: str, arg: object) -> bytes:
    """Produce the exact bytes for one target. Shared by write + check."""
    if kind == "svg":
        return art.master.encode("utf-8")
    if kind == "svg_dark":
        return art.master_dark.encode("utf-8")
    if kind == "svg_copy":
        return art.master.encode("utf-8")
    buf = io.BytesIO()
    if kind == "png":
        save_png(render(art.master if arg >= BADGE_MIN_SIZE else art.master_small, arg), buf)
    elif kind == "png_white":
        render(art.master, arg, background="#ffffff").convert("RGB").save(buf, "PNG", optimize=True)
    elif kind == "png_dark_white":
        render(art.master_dark, arg, background=DARK_HEX).convert("RGB").save(buf, "PNG", optimize=True)
    elif kind == "ico":
        img = render(art.master, max(arg))
        img.save(buf, format="ICO", sizes=[(s, s) for s in arg])
    elif kind == "logo":
        build_logo_image(art, dark=False).save(buf, "PNG")
    elif kind == "logo_dark":
        build_logo_image(art, dark=True).save(buf, "PNG")
    else:
        raise ValueError(f"unknown kind {kind!r}")
    return buf.getvalue()


def cmd_write(source: Path, out: Path) -> int:
    """Return failure when any target cannot be generated or verified."""
    art = IconArt(source)
    written = 0
    failures = 0
    for rel, kind, arg in TARGETS:
        path = out / rel
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            selected = art
            path.write_bytes(target_bytes(selected, kind, arg))
            written += 1
        except Exception as exc:
            failures += 1
            print(f"  !! {rel}: FAILED ({exc})")
    print(f"[write] wrote {written}/{len(TARGETS)} files")

    print("\n[verify]")
    for rel, kind, arg in TARGETS:
        path = out / rel
        try:
            if kind in ("svg", "svg_dark", "svg_copy", "icon_manifest"):
                print(f"  {rel}: {path.stat().st_size} bytes {'JSON' if kind == 'icon_manifest' else 'SVG'}")
                continue
            im = Image.open(path)
            if kind in ("ico", "ico_dark"):
                sizes = []
                try:
                    for i in range(im.n_frames):
                        im.seek(i)
                        sizes.append(im.size)
                except Exception:
                    sizes = [im.size]
                print(f"  {rel}: ICO {sorted(set(sizes))}")
            elif kind in ("icns", "icns_dark", "icns_from_png"):
                print(f"  {rel}: ICNS {im.size} (container)")
            else:
                print(f"  {rel}: {im.format} {im.size}")
        except Exception as exc:
            failures += 1
            print(f"  {rel}: VERIFY FAILED ({exc})")

    return int(failures > 0)


def cmd_check(source: Path, out: Path) -> int:
    """Structural verification: regenerate every target in memory and assert
    the invariants that actually matter (there are no committed bytes to
    byte-compare — outputs are generated on demand)."""
    art = IconArt(source)
    problems: list[str] = []

    # every target must generate without error
    for rel, kind, arg in TARGETS:
        try:
            selected = art
            target_bytes(selected, kind, arg)
        except Exception as exc:
            problems.append(f"{rel}: REGENERATE FAILED ({exc})")

    # PNG targets must have the expected format + size
    for rel, (fmt, size) in CHECK_SIZES.items():
        path = out / rel
        if not path.exists():
            problems.append(f"{rel}: MISSING (expected generated file)")
            continue
        try:
            im = Image.open(path)
            if im.format != fmt or im.size != size:
                problems.append(f"{rel}: got {im.format} {im.size}, expected {fmt} {size}")
        except Exception as exc:
            problems.append(f"{rel}: UNREADABLE ({exc})")

    if not problems:
        print(f"[ok] all {len(TARGETS)} targets generate and pass structural checks")
        return 0

    print(f"[check] {len(problems)} problem(s):")
    for line in problems:
        print(f"  - {line}")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--out", type=Path, help="Output root (defaults to source for developer builds)")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve()
    out = (args.out or source).resolve()
    command = cmd_check if args.check else cmd_write
    sys.exit(command(source, out))


if __name__ == "__main__":
    main()
