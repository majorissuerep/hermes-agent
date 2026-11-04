"""Public read-only encrypted artifact preview command."""
from __future__ import annotations


def build_artifacts_parser(subparsers):
    from hermes_cli.artifacts import add_view_arguments, view_command

    parser = subparsers.add_parser("artifacts", help="Preview an encrypted browser image without exporting it")
    actions = parser.add_subparsers(dest="artifact_action", required=True)
    view = actions.add_parser("view", help="Show a bounded in-memory grayscale image preview", allow_abbrev=False)
    add_view_arguments(view)
    view.set_defaults(func=view_command)
