"""Fetch and inventory a pinned upstream input without executing or merging its code."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
LOCAL_AREAS = ("agent/", "hermes_cli/", "hermes_platform/", "hermes_security/", "tools/", "cron/", "pm/", "skills/", "plugins/")
EXCLUDED_AREAS = ("gateway/", "apps/desktop/", "apps/dashboard/", "acp/", "web/", "web-ui/")


def git(repo: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=repo, text=True, encoding="utf-8", timeout=180).strip()


def inventory(repo: Path, *, fork: str = "origin/main", upstream: str = "upstream/main", limit: int = 20) -> dict:
    """Report graph and changed-area evidence; neither file parity nor qualification is inferred."""
    head = git(repo, "rev-parse", "--verify", "--end-of-options", f"{fork}^{{commit}}")
    source = git(repo, "rev-parse", "--verify", "--end-of-options", f"{upstream}^{{commit}}")
    base = git(repo, "merge-base", head, source)
    paths = git(repo, "diff", "--name-only", "-z", base, source).split("\0")
    paths = [p for p in paths if p]
    counts = {prefix: sum(path.startswith(prefix) for path in paths) for prefix in (*LOCAL_AREAS, *EXCLUDED_AREAS)}
    rows = git(repo, "log", f"--max-count={limit}", "--format=%H%x09%s", f"{head}..{source}", "--", *LOCAL_AREAS)
    return {
        "version": 1, "fork": head, "upstream": source, "shared_base": base,
        "upstream_commits": int(git(repo, "rev-list", "--count", f"{head}..{source}")),
        "changed_files": len(paths), "area_counts": counts,
        "local_port_candidates": [{"commit": row.split("\t", 1)[0], "subject": row.split("\t", 1)[1]}
                                  for row in rows.splitlines() if row],
        "excluded_product_areas": list(EXCLUDED_AREAS),
        "qualification": "pending: review and port against the encrypted local-runtime contract",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="refresh only upstream/main; never change the checkout")
    parser.add_argument("--fork", default="origin/main")
    parser.add_argument("--upstream", default="upstream/main")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--output", type=Path, help="write the source-only inventory as JSON")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100")
    if args.fetch:
        git(ROOT, "fetch", "--no-tags", "--no-recurse-submodules", "upstream", "main")
    result = inventory(ROOT, fork=args.fork, upstream=args.upstream, limit=args.limit)
    rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
