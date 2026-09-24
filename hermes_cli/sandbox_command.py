"""``/sandbox`` — per-session sandbox grants, shared by the CLI and the messaging gateway.

Surfaces only resolve the session key and render the reply; parsing, scanning and state
changes live here (the ``/goal`` pattern). File, network and socket grants take effect on
the NEXT spawned process (they never touch the prompt). Tool changes alter the tool schema,
so they apply to the next session — ``--now`` asks the surface to start one immediately,
exactly like ``/tools enable`` (never a mid-conversation cache break).
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

USAGE = """\
/sandbox                       show this session's sandbox (backend, tools, grants, network)
/sandbox grant PATH[:ro|:rw]   grant a file or directory (scanned first; --force to accept findings)
/sandbox revoke PATH           remove a grant (also one inherited from config or a preset)
/sandbox preset NAME           apply a preset (coding, research, workspace, toolchains, shell-rc, git, network, ...)
/sandbox presets               list presets
/sandbox tools [add|remove X]  list / change allowed tools (next session; --now starts one)
/sandbox net on|off            allow sandboxed processes to reach the network
/sandbox unix on|off           allow UNIX sockets (docker.sock / session bus are escapes)
/sandbox scan PATH             scan a path for secrets without granting it
/sandbox reset                 drop all of this session's changes"""


@dataclass(frozen=True)
class SandboxReply:
    text: str
    reset_session: bool = False


def _on_off(value: str) -> bool | None:
    return {"on": True, "yes": True, "true": True, "1": True,
            "off": False, "no": False, "false": False, "0": False}.get(value.lower())


def format_status(session_key: str) -> str:
    from hermes_security.sandbox.policy import sandbox_config
    from hermes_security.sandbox.session import effective_policy
    from hermes_security.sandbox.spawn import backend_status
    cfg = sandbox_config()
    backend = backend_status()
    lines = [f"Sandbox: {'ENABLED' if cfg.get('enabled') else 'disabled'}"
             f"  (backend: {backend['backend']} — {'ok' if backend['available'] else 'UNAVAILABLE'}: {backend['detail']})"]
    if not cfg.get("enabled"):
        lines.append("Enable it with `hermes sandbox enable` (takes effect for new sessions).")
    policy = effective_policy(session_key, with_tmp=bool(cfg.get("enabled")))
    lines.append(f"Presets: {', '.join(policy.presets) or '(none)'}")
    lines.append(f"Tools:   {', '.join(sorted(policy.tools)) or '(none — the model has no tools)'}")
    lines.append(f"Network: {'on' if policy.network else 'off'}   UNIX sockets: {'on' if policy.unix_sockets else 'off'}"
                 f"   Subagents: {policy.subagents}")
    grants = policy.expanded_grants()
    lines.append(f"Grants ({len(grants)}):" if grants else "Grants:  (none beyond the read-only OS baseline)")
    lines += [f"  {g.mode}  {g.path}" for g in sorted(grants, key=lambda g: g.path)]
    if policy.revoked:
        lines.append("Revoked: " + ", ".join(sorted(policy.revoked)))
    if policy.tmp_dir:
        lines.append(f"Private temp dir: {policy.tmp_dir}")
    return "\n".join(lines)


def _grant(session_key: str, args: list[str]) -> SandboxReply:
    from hermes_security.sandbox.policy import SandboxError, expand_path, parse_grant, sandbox_config
    from hermes_security.sandbox.scan import scan_path
    from hermes_security.sandbox.session import session_workspace, update_overrides
    force = "--force" in args
    specs = [a for a in args if a != "--force"]
    if not specs:
        return SandboxReply("Usage: /sandbox grant PATH[:ro|:rw] [--force]")
    lines = []
    for spec in specs:
        try:
            grant = parse_grant(spec)
        except SandboxError as exc:
            return SandboxReply(str(exc))
        paths = expand_path(grant.path, workspace=session_workspace())
        if sandbox_config().get("scan_on_grant", True) and not force:
            for path in paths:
                report = scan_path(path)
                if not report.clean:
                    return SandboxReply("\n".join([
                        f"Scan of {path} before granting it:", *report.summary_lines(),
                        f"Not granted. Re-run with --force to grant it anyway: /sandbox grant {spec} --force"]))
        def mutate(o, grant=grant, paths=paths):
            o.grants = [g for g in o.grants if parse_grant(g).path != grant.path] + [grant.spec]
            o.revoked = [r for r in o.revoked if not set(expand_path(r, workspace=session_workspace())) & set(paths)]
        update_overrides(session_key, mutate)
        lines.append(f"Granted {grant.mode} {', '.join(paths)} (takes effect on the next command).")
    return SandboxReply("\n".join(lines))


def _revoke(session_key: str, args: list[str]) -> SandboxReply:
    from hermes_security.sandbox.policy import expand_path, parse_grant
    from hermes_security.sandbox.session import session_workspace, update_overrides
    if not args:
        return SandboxReply("Usage: /sandbox revoke PATH")
    target = parse_grant(args[0]).path
    paths = set(expand_path(target, workspace=session_workspace()))
    def mutate(o):
        kept = [g for g in o.grants if not set(expand_path(parse_grant(g).path, workspace=session_workspace())) & paths]
        if len(kept) == len(o.grants) and target not in o.revoked:
            o.revoked.append(target)
        o.grants = kept
    update_overrides(session_key, mutate)
    return SandboxReply(f"Revoked {', '.join(sorted(paths))} for this session.")


def _preset(session_key: str, args: list[str]) -> SandboxReply:
    from hermes_security.sandbox.policy import SandboxError, resolve_presets, sandbox_config
    from hermes_security.sandbox.session import update_overrides
    if not args:
        return SandboxReply("Usage: /sandbox preset NAME")
    try:
        resolved = resolve_presets(args, sandbox_config().get("presets") or {})
    except SandboxError as exc:
        return SandboxReply(str(exc))
    update_overrides(session_key, lambda o: o.presets.extend(n for n in args if n not in o.presets))
    tools = sorted({t for _, p in resolved for t in p.tools})
    note = (f"\nTools from the preset ({', '.join(tools)}) apply to the next session — /sandbox tools add"
            " ... --now starts one." if tools else "")
    return SandboxReply(f"Applied preset(s): {', '.join(n for n, _ in resolved)}.{note}")


def format_presets() -> str:
    from hermes_security.sandbox.policy import preset_catalog, sandbox_config
    lines = ["Presets:"]
    for name, preset in sorted(preset_catalog(sandbox_config().get("presets") or {}).items()):
        extra = []
        if preset.include:
            extra.append("includes " + ", ".join(preset.include))
        if preset.grants:
            extra.append("grants " + ", ".join(preset.grants))
        if preset.tools:
            extra.append("tools " + ", ".join(preset.tools))
        if preset.network:
            extra.append("network on")
        lines.append(f"  {name:14} {preset.description}" + (f"  [{'; '.join(extra)}]" if extra else ""))
    return "\n".join(lines)


def _tools(session_key: str, args: list[str]) -> SandboxReply:
    from hermes_security.sandbox.session import effective_policy, update_overrides
    now = "--now" in args
    args = [a for a in args if a != "--now"]
    if not args:
        tools = sorted(effective_policy(session_key, with_tmp=False).tools)
        return SandboxReply("Allowed tools: " + (", ".join(tools) or "(none)"))
    action, names = args[0], args[1:]
    if action not in ("add", "remove") or not names:
        return SandboxReply("Usage: /sandbox tools add|remove NAME [NAME ...] [--now]")
    def mutate(o):
        if action == "add":
            o.tools = list(dict.fromkeys([*o.tools, *names]))
            o.tools_removed = [t for t in o.tools_removed if t not in names]
        else:
            o.tools = [t for t in o.tools if t not in names]
            o.tools_removed = list(dict.fromkeys([*o.tools_removed, *names]))
    update_overrides(session_key, mutate)
    verb = "Allowed" if action == "add" else "Removed"
    when = "Starting a new session so it takes effect." if now else "Takes effect next session (--now to start one)."
    return SandboxReply(f"{verb}: {', '.join(names)}. {when}", reset_session=now)


def _toggle(session_key: str, field: str, label: str, args: list[str]) -> SandboxReply:
    from hermes_security.sandbox.session import update_overrides
    value = _on_off(args[0]) if args else None
    if value is None:
        return SandboxReply(f"Usage: /sandbox {label} on|off")
    update_overrides(session_key, lambda o: setattr(o, field, value))
    return SandboxReply(f"{label} {'on' if value else 'off'} for this session (next command).")


def _scan(args: list[str]) -> SandboxReply:
    from hermes_security.sandbox.scan import scan_path
    if not args:
        return SandboxReply("Usage: /sandbox scan PATH")
    return SandboxReply("\n".join(scan_path(args[0]).summary_lines(limit=25)))


def dispatch_sandbox_command(args: str, *, session_key: str) -> SandboxReply:
    try:
        parts = shlex.split(args or "")
    except ValueError as exc:
        return SandboxReply(f"Could not parse arguments: {exc}")
    sub, rest = (parts[0].lower(), parts[1:]) if parts else ("status", [])
    handlers = {
        "status": lambda: SandboxReply(format_status(session_key)),
        "grant": lambda: _grant(session_key, rest),
        "revoke": lambda: _revoke(session_key, rest),
        "preset": lambda: _preset(session_key, rest),
        "presets": lambda: SandboxReply(format_presets()),
        "tools": lambda: _tools(session_key, rest),
        "net": lambda: _toggle(session_key, "network", "net", rest),
        "unix": lambda: _toggle(session_key, "unix_sockets", "unix", rest),
        "scan": lambda: _scan(rest),
        "reset": lambda: _reset(session_key),
        "help": lambda: SandboxReply(USAGE),
    }
    handler = handlers.get(sub)
    return handler() if handler else SandboxReply(f"Unknown subcommand '{sub}'.\n{USAGE}")


def _reset(session_key: str) -> SandboxReply:
    from hermes_security.sandbox.session import clear_overrides
    clear_overrides(session_key)
    return SandboxReply("This session's sandbox changes were dropped (config defaults apply).")
