"""Package a complete, contained terminal payload with source provenance."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tarfile
import tempfile
import tomllib


def _contained(root: Path, relative: str, *, directory: bool = False) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("payload paths must be relative")
    path = root / relative
    if not path.resolve(strict=True).is_relative_to(root):
        raise ValueError(f"path escapes payload: {relative}")
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError(f"incomplete payload: {relative}")
    return path


def inspect_payload(payload: Path, commit: str, target: str) -> str:
    """Reject dependency-only builds, wrong revisions and nonportable files."""
    root = payload.resolve(strict=True)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("an exact source commit is required")
    if not re.fullmatch(r"(?:linux|darwin)-(?:x64|arm64)", target):
        raise ValueError("release target must be native Linux or macOS")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or manifest.get("ref") != commit or manifest.get("target") != target:
        raise ValueError("payload manifest does not match the requested source and target")
    runtime = manifest["runtime"]
    repo = _contained(root, manifest["repo"], directory=True)
    for relative in (manifest["venv"], manifest["store"], runtime["repoDir"],
                     runtime["toolsDir"], runtime["sitePackages"], "pm-runtime", "uv-cache"):
        _contained(root, relative, directory=True)
    for relative in (runtime["storePython"], "pm-runtime/pm-runtime.json", "tools/facts.json"):
        _contained(root, relative)
    if _contained(root, runtime["repoDir"], directory=True) != repo:
        raise ValueError("runtime code must use the packaged repository")
    commands = runtime["commands"]
    if not isinstance(commands, dict) or "hermes" not in commands:
        raise ValueError("terminal launcher is missing")
    for relative in commands.values():
        _contained(root, relative)
    entry = _contained(root, f"{manifest['repo']}/hermes_cli/tui_dist/entry.js")
    package = _contained(root, f"{manifest['repo']}/hermes_cli/tui_dist/package.json")
    if not entry.stat().st_size or json.loads(package.read_text())["type"] != "module":
        raise ValueError("compiled terminal UI is incomplete")
    project = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+(?:[a-zA-Z0-9.+-]*)", version):
        raise ValueError("invalid release version")
    for path in root.rglob("*"):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            if Path(os.readlink(path)).is_absolute() or not path.resolve(strict=True).is_relative_to(root):
                raise ValueError(f"nonportable payload link: {path.relative_to(root)}")
        elif not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError(f"special file in payload: {path.relative_to(root)}")
    return version



MAX_ASSET_BYTES = 1_900_000_000  # Keep each release file below GitHub's 2 GiB limit.


class _ArchiveWriter:
    """Stream one gzip archive into bounded files without duplicating compressed storage."""
    def __init__(self, path: Path, maximum: int):
        if maximum < 1:
            raise ValueError("release asset bound must be positive")
        self.name, self.maximum = str(path), maximum
        self.original, self.paths = path, [path]
        self.handle = path.open("xb")
        self.part_size, self.size = 0, 0
        self.digest = hashlib.sha256()

    def _rotate(self):
        self.handle.close()
        if len(self.paths) == 1:
            first = self.original.with_name(self.original.name + ".part-001")
            self.paths[0].rename(first)
            self.paths[0] = first
        index = len(self.paths) + 1
        if index > 999:
            raise ValueError("release archive exceeds the bounded part catalogue")
        path = self.original.with_name(self.original.name + f".part-{index:03d}")
        self.handle = path.open("xb")
        self.paths.append(path)
        self.part_size = 0

    def write(self, data):
        view = memoryview(data)
        offset = 0
        while offset < len(view):
            if self.part_size == self.maximum:
                self._rotate()
            piece = view[offset:offset + self.maximum - self.part_size]
            written = self.handle.write(piece)
            if written != len(piece):
                raise OSError("incomplete release archive write")
            self.digest.update(piece)
            self.part_size += written
            self.size += written
            offset += written
        return len(view)

    def tell(self):
        return self.size

    def flush(self):
        self.handle.flush()

    def close(self):
        self.handle.close()


def _write_archive(payload, staged):
    writer = _ArchiveWriter(staged, MAX_ASSET_BYTES)
    try:
        with tarfile.open(fileobj=writer, mode="w:gz", compresslevel=6, dereference=False) as tar:
            tar.add(payload, arcname="hermes")
    finally:
        writer.close()
    parts = []
    for path in writer.paths:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        parts.append({"file": path.name, "size": path.stat().st_size, "sha256": digest})
    return writer, parts


def package_payload(payload: Path, out: Path, commit: str, target: str) -> Path:
    payload = payload.resolve(strict=True)
    out = out.resolve()
    if out.is_relative_to(payload) or payload.is_relative_to(out):
        raise ValueError("release output must be separate from the payload")
    version = inspect_payload(payload, commit, target)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"hermes-{version}-{target}-{commit[:12]}"
    archive, receipt = out / f"{stem}.tar.gz", out / f"{stem}.json"
    if archive.exists() or receipt.exists() or any(out.glob(archive.name + ".part-*")):
        raise FileExistsError("release output already exists")
    with tempfile.TemporaryDirectory(prefix=".package-", dir=out) as scratch:
        staged = Path(scratch) / archive.name
        writer, parts = _write_archive(payload, staged)
        data = {"schema": 1, "source": commit, "version": version, "target": target,
                "product": "terminal-agent", "archive": archive.name,
                "sha256": writer.digest.hexdigest(), "size": writer.size,
                "layout": "single" if len(parts) == 1 else "split", "parts": parts,
                "qualification": "build-only; native artifact lifecycle acceptance required"}
        staged_receipt = Path(scratch) / receipt.name
        staged_receipt.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        for part in writer.paths:
            os.link(part, out / part.name)
        os.link(staged_receipt, receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    print(package_payload(args.payload, args.out, args.commit, args.target))


if __name__ == "__main__":
    main()
