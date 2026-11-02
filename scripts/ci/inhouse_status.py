"""Read the existing repository CI identity's evidence for one immutable revision."""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from urllib.request import Request, urlopen

GITEA_API = "https://git.theincompetent.dev/api/v1/repos/unpossible/hermes-agent"
PUBLIC_API = "https://api.github.com/repos/majorissuerep/hermes-agent"
CONTEXTS = {f"ci/woodpecker/{event}/verify" for event in ("pr", "push", "manual")}


def read_json(url: str, token: str = ""):
    headers = {"Accept": "application/json", "User-Agent": "hermes-repository-ci"}
    if token:
        headers["Authorization"] = "token " + token
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        return json.load(response)


def latest_verification(rows: list[dict]) -> dict | None:
    """An older green, another writer or another CI workflow cannot qualify a head."""
    matches = [row for row in rows if row.get("context") in CONTEXTS
               and row.get("creator", {}).get("login") == "hermes-ci"
               and re.fullmatch(r"https://ci\.theincompetent\.dev/repos/17/pipeline/[1-9]\d*/[1-9]\d*",
                                row.get("target_url", ""))]
    return max(matches, key=lambda row: row["id"], default=None)


def github_qualified(rows: list[dict], sha: str) -> bool:
    matches = [row for row in rows if row.get("name") == "All required checks pass (v2)"
               and row.get("head_sha") == sha and row.get("app", {}).get("id") == 15368]
    latest = max(matches, key=lambda row: row["id"], default={})
    return latest.get("status") == "completed" and latest.get("conclusion") == "success"


def require_source_head(sha: str, token: str) -> None:
    if read_json(GITEA_API + "/branches/main", token)["commit"]["id"] != sha:
        raise RuntimeError("Source main moved; qualify its new revision before publishing")


def wait_for_verification(sha: str, token: str, timeout: int = 300) -> None:
    deadline = time.monotonic() + timeout
    while True:
        rows = read_json(GITEA_API + f"/statuses/{sha}?sort=highestindex&limit=50", token)
        row = latest_verification(rows)
        if row and row["status"] == "success":
            print(f"In-house verification passed for {sha}: {row['target_url']}", flush=True)
            return
        if row and row["status"] in {"failure", "error", "warning"}:
            raise RuntimeError(f"In-house verification did not pass for {sha}")
        if time.monotonic() >= deadline:
            raise TimeoutError("No completed in-house verification for the candidate revision")
        time.sleep(10)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        parser.error("sha must be a full immutable commit identity")
    token = os.environ.get("GITEA_STATUS_TOKEN", "")
    if not token:
        raise SystemExit("A read-only Gitea status credential is required")
    wait_for_verification(args.sha, token)


if __name__ == "__main__":
    main()
