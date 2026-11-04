"""Read-only, in-memory previews of the owning profile's encrypted browser images."""
from __future__ import annotations

import argparse
from io import BytesIO
import shlex
import sys

_MAX_COLUMNS, _MAX_ROWS = 120, 60
_MAX_INPUT_BYTES, _MAX_PIXELS = 20 * 1024 * 1024, 20_000_000


def _dimension(value, maximum):
    number = int(value)
    if not 1 <= number <= maximum:
        raise argparse.ArgumentTypeError(f"must be between 1 and {maximum}")
    return number


def add_view_arguments(parser):
    parser.add_argument("path", help="Own encrypted artifacts/browser image path")
    parser.add_argument("--cols", type=lambda value: _dimension(value, _MAX_COLUMNS), default=80)
    parser.add_argument("--rows", type=lambda value: _dimension(value, _MAX_ROWS), default=32)


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def validate_native_arguments(arguments):
    """Exact read-only grammar for operation-grant native supervision."""
    if arguments in ([], ["--help"], ["-h"], ["view", "--help"], ["view", "-h"]):
        return
    if not arguments or arguments[0] != "view":
        raise ValueError("borrowed artifacts authority permits only artifacts view")
    parser = _ArgumentParser(add_help=False, allow_abbrev=False)
    add_view_arguments(parser)
    parser.parse_args(arguments[1:])


def preview(path, *, cols=80, rows=32):
    """Authenticate in memory, bound decode and output, and emit only fixed palette characters."""
    from PIL import Image
    from agent.file_safety import get_model_read_block_error
    from hermes_constants import get_hermes_home
    from hermes_security import artifacts
    from hermes_security.artifact_paths import is_image_relative_path

    cols, rows = _dimension(cols, _MAX_COLUMNS), _dimension(rows, _MAX_ROWS)
    home = get_hermes_home()
    _, relative = artifacts._location(path, home)
    if not is_image_relative_path(relative):
        raise PermissionError("Preview permits only this profile's known browser image artifacts")
    if blocked := get_model_read_block_error(str(path)):
        raise PermissionError(blocked)
    data = artifacts.read_bytes(path, home=home)
    if len(data) > _MAX_INPUT_BYTES:
        raise ValueError("Image exceeds the 20 MiB preview input bound")
    with Image.open(BytesIO(data)) as image:
        if image.format not in {"PNG", "JPEG", "WEBP", "GIF"}:
            raise ValueError("Unsupported image format for private preview")
        width, height = image.size
        if width * height > _MAX_PIXELS:
            raise ValueError("Image exceeds the 20 million pixel preview bound")
        factor = min(cols / width, rows * 2 / height, 1)
        size = max(1, int(width * factor)), max(1, int(height * factor / 2))
        gray = image.convert("L").resize(size, Image.Resampling.LANCZOS)
        pixels = gray.tobytes()
    palette = " .:-=+*#%@"
    lines = ["".join(palette[value * (len(palette) - 1) // 255] for value in pixels[start:start + size[0]])
             for start in range(0, len(pixels), size[0])]
    return (f"Bounded grayscale preview ({width}×{height} pixels; {size[0]}×{size[1]} text cells).\n"
            "Encrypted original retained; vision tools can inspect the full image.\n```text\n"
            + "\n".join(lines) + "\n```")


def view_command(args):
    from PIL import Image
    from hermes_security.errors import VaultError

    try:
        print(preview(args.path, cols=args.cols, rows=args.rows))
    except (OSError, ValueError, VaultError, Image.DecompressionBombError) as exc:
        print(f"Artifact preview unavailable: {exc}", file=sys.stderr)
        return 1
    return 0


def slash_command(command):
    parser = _ArgumentParser(prog="/artifacts", allow_abbrev=False)
    sub = parser.add_subparsers(dest="action", required=True).add_parser("view", allow_abbrev=False)
    add_view_arguments(sub)
    try:
        arguments = shlex.split(command)[1:]
        if arguments in (["--help"], ["-h"]):
            print(parser.format_help())
            return 0
        if arguments in (["view", "--help"], ["view", "-h"]):
            print(sub.format_help())
            return 0
        args = parser.parse_args(arguments)
        return view_command(args)
    except (ValueError, argparse.ArgumentTypeError) as exc:
        print(f"Artifact preview unavailable: {exc}")
        return 2

