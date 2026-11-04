"""Reproduce the maintained Pillow compatibility sdists from verified upstream inputs.

Download the two exact archives in pm/compatibility/provenance.json into a disposable
directory, then run this script with --input-dir DIR. --check verifies committed bytes.
No upstream runtime code is changed and no network operation occurs in this builder.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile


def build_archive(data: bytes, recipe: dict) -> bytes:
    """Apply only declared, count-checked metadata patches to an aged source archive."""
    if hashlib.sha256(data).hexdigest() != recipe["sha256"]:
        raise ValueError("upstream archive hash differs from reviewed provenance")
    changed = set()
    old_root = recipe["filename"].removesuffix(".tar.gz")
    new_root = recipe["output"].removesuffix(".tar.gz")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as upstream:
        with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as result:
            for member in upstream:
                path = PurePosixPath(member.name)
                if (path.is_absolute() or ".." in path.parts or path.parts[0] != old_root
                        or not (member.isfile() or member.isdir())):
                    raise ValueError("unsafe upstream archive member")
                relative = path.relative_to(old_root).as_posix()
                body = upstream.extractfile(member).read() if member.isfile() else b""
                for old, new, count in recipe["replacements"].get(relative, []):
                    if body.count(old.encode()) != count:
                        raise ValueError(f"unexpected metadata in {relative}")
                    body = body.replace(old.encode(), new.encode())
                    changed.add(relative)
                target = tarfile.TarInfo(str(PurePosixPath(new_root) / relative))
                target.type = tarfile.REGTYPE if member.isfile() else tarfile.DIRTYPE
                target.mode = member.mode
                target.size = len(body) if member.isfile() else 0
                result.addfile(target, io.BytesIO(body) if member.isfile() else None)
    if changed != set(recipe["replacements"]):
        raise ValueError("a declared metadata input is missing")
    # Stored DEFLATE blocks avoid byte drift between zlib and zlib-ng hosts.
    return gzip.compress(buffer.getvalue(), compresslevel=0, mtime=0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    destination = Path(__file__).resolve().parents[1] / "pm" / "compatibility"
    manifest = json.loads((destination / "provenance.json").read_text(encoding="utf-8"))
    for recipe in manifest["packages"]:
        data = build_archive((args.input_dir / recipe["filename"]).read_bytes(), recipe)
        digest = hashlib.sha256(data).hexdigest()
        if recipe.get("output_sha256") and recipe["output_sha256"] != digest:
            raise ValueError("rebuilt archive differs from reviewed output")
        target = destination / recipe["output"]
        if args.check:
            if target.read_bytes() != data:
                raise ValueError(f"committed archive differs: {target.name}")
        else:
            target.write_bytes(data)
        print(f"{target.name} sha256:{digest}")


if __name__ == "__main__":
    main()
