"""Keep the existing critical-source, dependency-bound and infographic guards forge independent."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.code_health import gitio

_RASTER = re.compile(r"(?:^|/)(?:infograph|infograf)[^/]*/.*\.(?:png|jpe?g|webp|gif)$", re.IGNORECASE)
_UNBOUNDED = re.compile(r'"[a-zA-Z0-9_-]+(?:\[[^\]]*\])?>=\s*[0-9.]+\s*"')
_SOURCE_RULES = (
    ("decoded-code-execution", re.compile(r"base64\.(?:b64decode|decodebytes|urlsafe_b64decode)", re.IGNORECASE),
     re.compile(r"\b(?:exec|eval)\s*\(")),
    ("encoded-subprocess", re.compile(r"\bsubprocess\.(?:Popen|call|run)\s*\("),
     re.compile(r"base64|\\x[0-9a-f]{2}|chr\(", re.IGNORECASE)),
)


def audit(repo: Path, base: str, head: str | None) -> list[tuple[str, str]]:
    """Scan added source lines and the candidate's tracked raster paths; never print payloads."""
    findings = {(path, "committed-infographic") for path in gitio.tracked_files(repo, head) if _RASTER.search(path)}
    arguments = ["diff", "--no-ext-diff", "--no-color", "--unified=0", base]
    if head is not None:
        arguments.append(head)
    diff = gitio.git(repo, *arguments, "--", ".", ":!uv.lock", ":!*.lock", ":!package-lock.json", ":!yarn.lock")
    path = ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            findings.update((path, name) for name, first, second in _SOURCE_RULES
                            if first.search(line) and second.search(line))
            if path == "pyproject.toml" and _UNBOUNDED.search(line):
                findings.add((path, "unbounded-dependency"))
    return sorted(findings)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base")
    parser.add_argument("--head")
    args = parser.parse_args(argv)
    try:
        head = gitio.resolve_tree(ROOT, args.head) if args.head else None
        base = gitio.resolve_rev(ROOT, args.base) if args.base else gitio.default_base(ROOT)
        findings = audit(ROOT, base, head)
    except RuntimeError as exc:
        print(f"source policy could not run: {exc}", file=sys.stderr)
        return 2
    for path, rule in findings:
        print(f"{path}: {rule}")
    print(f"source policy: {len(findings)} finding(s)")
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
