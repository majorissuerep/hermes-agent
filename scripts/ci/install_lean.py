"""Install the reviewed official Lean toolchain into a new disposable directory."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hermes_platform.host.facts import native_arch, os_family

VERSION = "4.35.0"
DIGESTS = {
    ("linux", "amd64"): ("linux", "7289597a44543246c9227b8cae501767e095ce17f6d448b95fc4a8dc97268f6d"),
    ("linux", "arm64"): ("linux_aarch64", "c8f0f8a3920e839718b1343e6781fd80a3e9b1024fbcd7078eb30f54e806b028"),
    ("darwin", "amd64"): ("darwin", "0b43be5c7ecfb36016566a41005ab62bdb04743e6bfd5837e45f796ef4750b4e"),
    ("darwin", "arm64"): ("darwin_aarch64", "c0c6f34f35ba1dcc5c4a5fd106fefab9eb5e8469712624dd2c868d51b63177c2"),
}


def install(directory: Path) -> Path:
    key = (os_family(), native_arch())
    if key not in DIGESTS:
        raise RuntimeError("Formal logic qualification requires a supported native Linux/macOS architecture")
    suffix, expected = DIGESTS[key]
    directory.mkdir(parents=True, exist_ok=False)
    filename = f"lean-{VERSION}-{suffix}.tar.zst"
    archive = directory / filename
    digest, deadline, total = hashlib.sha256(), time.monotonic() + 300, 0
    url = f"https://github.com/leanprover/lean4/releases/download/v{VERSION}/{filename}"
    with urlopen(url, timeout=30) as response, archive.open("xb") as output:
        while block := response.read(4 * 1024 * 1024):
            total += len(block)
            if total > 700_000_000 or time.monotonic() > deadline:
                raise RuntimeError("Lean toolchain download exceeded its bound")
            digest.update(block)
            output.write(block)
    if digest.hexdigest() != expected:
        raise RuntimeError("Official Lean toolchain archive digest mismatch")
    subprocess.run(["tar", "--zstd", "-xf", str(archive), "-C", str(directory)],
                   check=True, timeout=180)
    archive.unlink()
    executable = directory / f"lean-{VERSION}-{suffix}" / "bin" / "lean"
    subprocess.run([str(executable), "--version"], stdin=subprocess.DEVNULL,
                   check=True, timeout=10, stdout=sys.stderr)
    return executable


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True, help="a new directory; never overwrites a toolchain")
    args = parser.parse_args()
    print(install(args.directory.resolve()))


if __name__ == "__main__":
    main()
