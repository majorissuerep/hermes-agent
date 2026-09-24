"""``hermes sandbox`` subcommand parser."""

from __future__ import annotations


def build_sandbox_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "sandbox", help="OS-native default-deny sandbox for model tools, files and processes",
        description="Confine everything the model runs (terminal, file tools, background processes, "
        "execute_code, subagents) with Landlock+seccomp (Linux) or Seatbelt (macOS), and expose only "
        "allow-listed tools. These commands change config.yaml (new sessions); use /sandbox inside "
        "a session for per-session grants.")
    sub = parser.add_subparsers(dest="sandbox_command")
    sub.add_parser("status", help="Show backend, presets, tools and grants")
    enable = sub.add_parser("enable", help="Enable the sandbox for new sessions")
    enable.add_argument("--preset", action="append", help="Add a default preset (repeatable)")
    sub.add_parser("disable", help="Disable the sandbox for new sessions")
    grant = sub.add_parser("grant", help="Grant PATH[:ro|:rw] to every session (scanned first)")
    grant.add_argument("path")
    grant.add_argument("--force", action="store_true", help="Grant even when the scan finds secrets")
    revoke = sub.add_parser("revoke", help="Remove a configured grant")
    revoke.add_argument("path")
    for name, noun in (("tools", "tool/toolset names"), ("preset", "preset names")):
        edit = sub.add_parser(name, help=f"List/add/remove default {noun}")
        edit.add_argument("action", nargs="?", default="list", choices=["list", "add", "remove"])
        edit.add_argument("names", nargs="*")
    sub.add_parser("presets", help="List built-in and custom presets")
    scan = sub.add_parser("scan", help="Scan a path for secrets and credential files")
    scan.add_argument("path")
    sub.add_parser("check", help="Prove confinement live on this machine")
    run = sub.add_parser("run", help="Run a command inside the current session policy")
    run.add_argument("--preset", action="append")
    run.add_argument("--grant", action="append", help="Extra PATH[:rw] for this run")
    run.add_argument("--net", action="store_true", help="Allow network for this run")
    run.add_argument("--explain", action="store_true", help="Print the effective launch spec first")
    run.add_argument("run_command", nargs="...", metavar="COMMAND")
    setup = sub.add_parser("setup", help="Guided setup: pick default presets, enable, self-check")
    setup.set_defaults(preset=None)

    def _run(args):
        from hermes_cli.sandbox_cmd import cmd_sandbox
        return cmd_sandbox(args)

    parser.set_defaults(func=_run)
