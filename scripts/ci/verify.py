"""One bounded verification command for local checkouts and either CI forge."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.ci.classify_changes import classify

EXTRAS = ["all", "icons", "anthropic", "bedrock", "mistral", "fal", "modal", "daytona", "parallel-web", "lancedb-memory"]


def run(argv: list[str], env: dict[str, str]) -> None:
    print("+ " + " ".join(argv), flush=True)
    subprocess.run(argv, cwd=ROOT, env=env, check=True, timeout=7200)


def git(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=ROOT,
                                   text=True, encoding="utf-8", timeout=30).strip()


def require_clean_checkout(head: str) -> None:
    dirty = git("status", "--porcelain=v1", "--untracked-files=normal")
    if dirty:
        raise SystemExit("Verification requires a clean committed checkout:\n" + dirty)
    if git("rev-parse", "HEAD") != head:
        raise SystemExit("The checkout revision changed during verification.")


def plan(paths: list[str], event: str) -> dict[str, bool]:
    """Unknown/no diff and CI edits fail open; ordinary PRs reuse the classifier."""
    if event != "pull_request" or any(p.startswith((".woodpecker/", "scripts/ci/")) for p in paths):
        paths = []
    lanes = classify(paths)
    return {name: lanes[name] for name in ("python", "frontend", "site")}


def setup(env: dict[str, str], directory: Path) -> None:
    """Consume PM's existing file-command API as data, never shell-evaluate it."""
    files = {name: directory / name for name in ("GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT")}
    env.update({name: str(path) for name, path in files.items()})
    for phase in ("prepare", "install", "dependencies"):
        for path in files.values():
            path.write_text("", encoding="utf-8")
        run([
            env.get("HERMES_PYTHON", sys.executable), "-S", "scripts/ci/setup_toolchain.py", phase,
            "--toolchain", "all", "--home", env["HERMES_HOME"],
            "--extras", json.dumps(EXTRAS), "--test-environment", "--packages", "ripgrep",
        ], env)
        for line in files["GITHUB_ENV"].read_text(encoding="utf-8").splitlines():
            key, value = line.split("=", 1)
            env[key] = value
        for path in files["GITHUB_PATH"].read_text(encoding="utf-8").splitlines():
            env["PATH"] = path + os.pathsep + env["PATH"]
    for name in files:
        env.pop(name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", help="show selected consumers without provisioning or running checks")
    parser.add_argument("--base", help="reviewed Git base; default is the PR merge base or HEAD's first parent")
    parser.add_argument("--event", choices=("pull_request", "push", "manual"))
    args = parser.parse_args()
    env = dict(os.environ)
    event = args.event or env.get("CI_PIPELINE_EVENT") or env.get("GITHUB_EVENT_NAME", "manual")
    base = args.base or "HEAD^1"
    if event == "pull_request" and args.base is None:
        base = git("merge-base", "HEAD", "origin/main")
    base = git("rev-parse", "--verify", "--end-of-options", base + "^{commit}")
    head = git("rev-parse", "HEAD")
    paths = git("diff", "--name-only", base, head).splitlines()
    lanes = plan(paths, event)
    print(f"Verification revision: {head}; base: {base}", flush=True)
    print("Validation lanes: " + json.dumps(lanes), flush=True)
    if args.plan:
        return
    require_clean_checkout(head)
    with tempfile.TemporaryDirectory(prefix="hermes-inhouse-") as temporary:
        directory = Path(temporary)
        env.update({
            "HERMES_HOME": str(directory / "home"), "HERMES_RUNTIME_DIR": str(directory / "tools"),
            "TMPDIR": str(directory / "tmp"), "HERMES_TEST_WORKERS": "2",
            "HERMES_TEST_FILE_RETRIES": "0", "HERMES_TEST_FILE_TIMEOUT": "600", "VITEST_MAX_WORKERS": "2",
        })
        for name in ("HERMES_TEST_PATHS", "HERMES_TEST_SLICE"):
            env.pop(name, None)
        env.setdefault("NODE_OPTIONS", "--max-old-space-size=2048")
        Path(env["TMPDIR"]).mkdir()
        setup(env, directory)
        python = env["HERMES_PYTHON"]
        run([python, "-c", "import sqlite3, hermes_state.wal as w; "
             "print('SQLite', sqlite3.sqlite_version); "
             "assert not w.is_sqlite_wal_reset_vulnerable(), 'PM Python must qualify WAL tests'"], env)
        run([python, "scripts/check", "--base", base], env)
        run([python, "scripts/gen-bootstrap-pins.py", "--check"], env)
        if classify(paths)["uv_lock"]:
            run([python, "-m", "pm.build_env", "--source", ".", "--check-lock"], env)
        run(["node", "scripts/generate-icons.mjs", "--check"], env)
        if lanes["frontend"] or lanes["python"]:
            run(["npm", "ci"], env)
            run(["npm", "run", "build", "--prefix", "ui-tui"], env)
        if lanes["python"]:
            run(["scripts/run_tests.sh", "-j", "2", "--file-retries", "0"], env)
        if lanes["frontend"]:
            run(["node", ".github/scripts/run-workspace-checks.mjs", "--concurrency", "1"], env)
        if lanes["site"]:
            run(["npm", "ci", "--prefix", "website"], env)
            for script in ("extract-skills.py", "generate-skill-docs.py", "generate-llms-txt.py",
                           "extract-automation-blueprints.py", "extract-plugins.py", "check_doc_links.py"):
                run([python, "website/scripts/" + script], env)
            run([python, "-m", "scripts.ci.python_packages", "ascii-guard==2.3.0", "--", "-c",
                 "from ascii_guard.cli import main; raise SystemExit(main())",
                 "lint", "--exclude-code-blocks", "website/docs"], env)
            run(["npm", "run", "typecheck", "--prefix", "website"], env)
            run(["npm", "run", "build:fast", "--prefix", "website"], env)
        require_clean_checkout(head)


if __name__ == "__main__":
    main()
