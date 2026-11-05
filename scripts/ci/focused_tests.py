"""Small default fork checks; the complete regression suite remains explicit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BATCHES = (
    ("encrypted ownership", ("tests/hermes_security/test_session_authority.py",), ()),
    ("managed delegation", ("tests/tools/test_async_delegation.py",),
     ("-k", "completion_event_lands_on_shared_queue_with_session_key or "
      "dispatch_rejected_at_capacity or interrupt_all_signals_running_children")),
    ("formal repair feedback", ("tests/agent/test_formal_logic_checker_diagnostics.py",), ()),
    ("context preservation", ("tests/agent/test_context_chunk_selection_contracts.py",
                              "tests/agent/test_context_overflow_reported_budget.py"), ()),
    ("encrypted conversation and resume", ("tests/scripts/e2e/test_provider_conversation_chat.py",),
     ("-k", "litellm")),
    ("verification selection", ("tests/ci/test_focused_tests.py",), ()),
)


def commands(profile: str, root: Path = ROOT) -> list[list[str]]:
    """Validate every selected file before starting any subprocess."""
    if profile == "full":
        return [["scripts/run_tests.sh", "-j", "1", "--file-retries", "0"]]
    if profile != "focused":
        raise ValueError(f"unknown test profile: {profile}")
    missing = [name for _, files, _ in BATCHES for name in files if not (root / name).is_file()]
    if missing:
        raise ValueError("focused test inputs are missing: " + ", ".join(missing))
    return [
        ["scripts/run_tests.sh", "-j", "1", "--file-retries", "0",
         "--file-timeout", "600", *files, *(["--", *options] if options else [])]
        for _, files, options in BATCHES
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("focused", "full"), default="focused")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args()
    selected = commands(args.profile)
    print(json.dumps({"profile": args.profile, "workers": 1, "retries": 0,
                      "commands": selected}, indent=2), flush=True)
    if args.plan:
        return
    for command in selected:
        subprocess.run(command, cwd=ROOT, check=True,
                       timeout=7200 if args.profile == "full" else 1200)


if __name__ == "__main__":
    main()
