#!/usr/bin/env python3
"""Measure the frame backing store against a specified checkout; never open live state."""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tree", type=Path)
    parser.add_argument("--repetitions", type=int, choices=range(1, 6), default=3)
    args = parser.parse_args()
    tree = args.tree.resolve(strict=True)
    sha = subprocess.check_output(["git", "-C", str(tree), "rev-parse", "HEAD"], text=True).strip()
    dirty = bool(subprocess.check_output(["git", "-C", str(tree), "status", "--porcelain"]))
    sys.path.insert(0, str(tree))
    from hermes_security import frames, vault
    if not Path(frames.__file__).resolve().is_relative_to(tree):
        raise RuntimeError("benchmark imported frames outside the requested checkout")

    rows = []
    payload = b"synthetic-frame-payload:" + b"x" * 233
    for count in (100, 1000, 10000):
        for repetition in range(args.repetitions):
            with tempfile.TemporaryDirectory(prefix="hermes-frame-benchmark-") as directory:
                home = Path(directory)
                vault.init_vault(home, "synthetic-benchmark-only")
                vault.unlock(home, "synthetic-benchmark-only")
                path = home / "frames.log"
                path.write_bytes(frames.encode_frames([payload] * count, vault=vault.get_vault(home), purpose="log"))
                timings = []
                for _ in range(21):
                    start = time.perf_counter()
                    frames.append(path, payload, purpose="log")
                    timings.append((time.perf_counter() - start) * 1000)
                reopened = list(frames.read_frames(path, purpose="log"))
                if reopened != [payload] * (count + len(timings)):
                    raise RuntimeError("an acknowledged payload did not survive reopen")
                rows.append({"records": count, "repetition": repetition,
                             "bytes": path.stat().st_size,
                             "cold_ms": timings[0], "warm_median_ms": statistics.median(timings[1:]),
                             "warm_max_ms": max(timings[1:]), "samples_ms": timings})
                vault.clear_vault_cache()
    print(json.dumps({"scope": "encrypted-frame-append-only", "sha": sha,
                      "dirty": dirty, "tree": str(tree), "python": sys.version,
                      "payload_bytes": len(payload), "rows": rows}, indent=2))


if __name__ == "__main__":
    main()
