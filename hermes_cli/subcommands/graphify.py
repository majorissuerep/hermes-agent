"""``hermes graphify`` uses the bundled runtime without terminal Python setup."""

from __future__ import annotations

import runpy
from pathlib import Path


def _runner() -> dict:
    from hermes_constants import get_bundled_skills_dir

    skills = get_bundled_skills_dir(Path(__file__).resolve().parents[2] / "skills")
    return runpy.run_path(str(skills / "software-development" / "graphify" / "scripts" / "project_graph.py"))


def cmd_graphify(args) -> int:
    args.action = args.graphify_action
    return _runner()["execute"](args)


def build_graphify_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "graphify", help="Build and query reusable project knowledge graphs",
        description="Index code locally and reuse document relationships extracted by Hermes. "
                    "Indexes live beside project sources; no Hermes credentials or state are read.")
    _runner()["configure_parser"](parser, action_dest="graphify_action")
    parser.set_defaults(func=cmd_graphify)
