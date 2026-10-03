"""``hermes graphify`` uses the bundled runtime without terminal Python setup."""

from __future__ import annotations

import argparse
import runpy
from pathlib import Path


def configure_parser(parser: argparse.ArgumentParser, *, action_dest: str = "action") -> None:
    subparsers = parser.add_subparsers(dest=action_dest, required=True)
    subparsers.add_parser("scan").add_argument("project", type=Path)
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("project", type=Path)
    build_parser.add_argument("--semantic", type=Path)
    build_parser.add_argument("--force", action="store_true")
    for action, fields in {"query": ("question",), "explain": ("concept",), "path": ("start", "end")}.items():
        query_parser = subparsers.add_parser(action)
        query_parser.add_argument("project", type=Path)
        for field in fields:
            query_parser.add_argument(field)
        if action == "query":
            query_parser.add_argument("--budget", type=int, default=2000)


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
    configure_parser(parser, action_dest="graphify_action")
    parser.set_defaults(func=cmd_graphify)
