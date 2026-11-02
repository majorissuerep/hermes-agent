"""Qualify and fast-forward the existing public mirror without changing protection."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time

from scripts.ci.inhouse_status import PUBLIC_API, github_qualified, read_json, require_source_head

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_REMOTE = "ssh://git@ssh.github.com:443/majorissuerep/hermes-agent.git"


def git(*args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, env=env, text=True, timeout=120).strip()


def publish(sha: str, env: dict[str, str], token: str, remote: str = PUBLIC_REMOTE, timeout: int = 2100) -> None:
    """Only the current source head, descended from public main, may advance it."""
    if git("rev-parse", "HEAD") != sha or git("status", "--porcelain=v1"):
        raise RuntimeError("Publishing requires the clean, verified source revision")
    require_source_head(sha, token)
    git("fetch", "--no-tags", remote, "refs/heads/main", env=env)
    git("merge-base", "--is-ancestor", "FETCH_HEAD", sha)
    # This ordinary branch starts native host checks. It never changes public main.
    git("push", remote, f"{sha}:refs/heads/mirror/qualification", env=env)
    deadline = time.monotonic() + timeout
    while True:
        rows = read_json(PUBLIC_API + f"/commits/{sha}/check-runs?per_page=100")["check_runs"]
        if github_qualified(rows, sha):
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("GitHub's required application-bound check has not passed")
        time.sleep(60)
    require_source_head(sha, token)
    # No force, bypass, protection edits or success-status synthesis.
    git("push", "--atomic", remote, f"{sha}:refs/heads/main", env=env)
    observed = git("ls-remote", remote, "refs/heads/main", env=env).split()[0]
    if observed != sha:
        raise RuntimeError("Public mirror readback differs from the qualified source")
    require_source_head(sha, token)
    print(f"Public main matches qualified source {sha}", flush=True)


def main() -> None:
    sha = os.environ.get("CI_COMMIT_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise SystemExit("CI_COMMIT_SHA must name the checked-out immutable revision")
    token = os.environ.get("GITEA_STATUS_TOKEN", "")
    key = os.environ.get("GITHUB_PUBLISH_KEY", "")
    if not token or not key:
        raise SystemExit("The scoped source-read credential and mirror deploy key are required")
    with tempfile.TemporaryDirectory(prefix="hermes-public-mirror-") as temporary:
        directory = Path(temporary)
        key_path = directory / "deploy-key"
        key_path.touch(mode=0o600)
        key_path.write_text(key + "\n", encoding="utf-8")
        hosts = directory / "known_hosts"
        metadata = read_json("https://api.github.com/meta")
        hosts.write_text("\n".join("[ssh.github.com]:443 " + host
                                   for host in metadata["ssh_keys"]) + "\n", encoding="utf-8")
        env = dict(os.environ)
        env.pop("GITHUB_PUBLISH_KEY", None)
        env.pop("GITEA_STATUS_TOKEN", None)
        env["GIT_SSH_COMMAND"] = shlex.join([
            "ssh", "-F", "/dev/null", "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={hosts}", "-i", str(key_path),
        ])
        publish(sha, env, token)


if __name__ == "__main__":
    main()
