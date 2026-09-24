"""``hermes sandbox`` — persistent sandbox configuration, live self-check, and a runner.

Persistent settings live in ``sandbox:`` of config.yaml (applied to every NEW session);
per-session changes are the ``/sandbox`` slash command (:mod:`hermes_cli.sandbox_command`).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _load():
    from hermes_cli.config import load_config
    cfg = load_config()
    section = cfg.get("sandbox")
    if not isinstance(section, dict):
        section = cfg["sandbox"] = {}
    return cfg, section


def _save(cfg) -> None:
    from hermes_cli.config import save_config
    save_config(cfg)


def _require_backend() -> bool:
    from hermes_security.sandbox.spawn import backend_status
    status = backend_status()
    if not status["available"]:
        print(f"✗ The OS sandbox is not available on this host: {status['detail']}")
        return False
    return True


def _status(args) -> int:
    from hermes_cli.sandbox_command import format_status
    from hermes_security.sandbox.session import current_session_key
    print(format_status(current_session_key()))
    return 0


def _enable(args) -> int:
    from hermes_security.sandbox.policy import SandboxError, resolve_presets
    if not _require_backend():
        return 1
    cfg, section = _load()
    presets = list(dict.fromkeys([*(section.get("default_presets") or []), *(args.preset or [])]))
    try:
        resolve_presets(presets, section.get("presets") or {})
    except SandboxError as exc:
        print(f"✗ {exc}")
        return 1
    section["enabled"] = True
    section["default_presets"] = presets
    _save(cfg)
    print(f"✓ Sandbox enabled for new sessions. Default presets: {', '.join(presets) or '(none)'}")
    if not presets and not section.get("tools"):
        print("  Sessions start with NO tools and NO file access. Add presets with "
              "`hermes sandbox preset add coding` or per session with `/sandbox preset coding`.")
    return 0


def _disable(args) -> int:
    cfg, section = _load()
    section["enabled"] = False
    _save(cfg)
    print("Sandbox disabled for new sessions (model processes run unconfined).")
    return 0


def _grant(args) -> int:
    from hermes_security.sandbox.policy import expand_path, parse_grant
    from hermes_security.sandbox.scan import scan_path
    cfg, section = _load()
    grant = parse_grant(args.path)
    if section.get("scan_on_grant", True) and not args.force:
        for path in expand_path(grant.path):
            report = scan_path(path)
            print("\n".join(report.summary_lines(limit=25)))
            if not report.clean:
                print(f"✗ Not granted. Re-run with --force to grant it anyway.")
                return 1
    grants = [g for g in (section.get("grants") or []) if parse_grant(g).path != grant.path]
    section["grants"] = [*grants, grant.spec]
    _save(cfg)
    print(f"✓ Granted {grant.mode} {grant.path} to every new session.")
    return 0


def _revoke(args) -> int:
    from hermes_security.sandbox.policy import parse_grant
    cfg, section = _load()
    target = parse_grant(args.path).path
    grants = section.get("grants") or []
    kept = [g for g in grants if parse_grant(g).path != target]
    if len(kept) == len(grants):
        print(f"{target} is not a configured grant (presets grant paths too: `hermes sandbox status`).")
        return 1
    section["grants"] = kept
    _save(cfg)
    print(f"✓ Revoked {target}.")
    return 0


def _list_edit(key: str, noun: str, validate=None):
    def run(args) -> int:
        cfg, section = _load()
        items = list(section.get(key) or [])
        if args.action == "list":
            print(f"{noun}: {', '.join(items) or '(none)'}")
            return 0
        if validate is not None and args.action == "add":
            error = validate(args.names, section)
            if error:
                print(f"✗ {error}")
                return 1
        if args.action == "add":
            items = list(dict.fromkeys([*items, *args.names]))
        else:
            items = [i for i in items if i not in args.names]
        section[key] = items
        _save(cfg)
        print(f"✓ {noun}: {', '.join(items) or '(none)'} (new sessions)")
        return 0
    return run


def _validate_presets(names, section):
    from hermes_security.sandbox.policy import SandboxError, resolve_presets
    try:
        resolve_presets(names, section.get("presets") or {})
    except SandboxError as exc:
        return str(exc)
    return None


def _presets(args) -> int:
    from hermes_cli.sandbox_command import format_presets
    print(format_presets())
    return 0


def _scan(args) -> int:
    from hermes_security.sandbox.scan import scan_path
    report = scan_path(args.path)
    print("\n".join(report.summary_lines(limit=50)))
    return 0 if report.clean else 1


def _probe(spec: dict, shell: str) -> tuple[int, str]:
    from hermes_security.sandbox.spawn import wrap_argv
    proc = subprocess.run(wrap_argv(["/bin/sh", "-c", shell], spec), capture_output=True, text=True, timeout=60)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _check(args) -> int:
    """Prove confinement live on this machine with a throwaway policy."""
    from hermes_constants import get_hermes_home
    from hermes_security.sandbox.policy import SandboxPolicy, Grant
    if not _require_backend():
        return 1
    home = os.path.expanduser("~")
    with tempfile.TemporaryDirectory(prefix="hermes-sandbox-check-") as work:
        granted = Path(work, "granted")
        outside = Path(work, "outside")
        granted.mkdir()
        outside.mkdir()
        (granted / "in.txt").write_text("granted\n")
        (outside / "secret.txt").write_text("secret\n")
        policy = SandboxPolicy(enabled=True, grants=(Grant(str(granted), "rw"),))
        spec = policy.launch_spec()
        q = lambda p: "'" + str(p).replace("'", "'\\''") + "'"  # noqa: E731
        probes = [
            ("read a granted file", f"cat {q(granted / 'in.txt')}", True),
            ("write inside a rw grant", f"echo x > {q(granted / 'new.txt')}", True),
            ("read outside the grants", f"cat {q(outside / 'secret.txt')}", False),
            ("write outside the grants", f"echo x > {q(outside / 'evil.txt')}", False),
            ("list the home directory", f"ls {q(home)}", False),
            ("read the Hermes home", f"ls {q(get_hermes_home())}", False),
            ("create a UNIX socket (docker.sock / session bus escape)",
             f"{q(sys.executable)} -I -S -c 'import socket; socket.socket(socket.AF_UNIX)'", False),
            ("open a network socket with network off",
             f"{q(sys.executable)} -I -S -c 'import socket; socket.socket(socket.AF_INET)'", False),
        ]
        spec["read"].append(os.path.dirname(os.path.dirname(os.path.realpath(sys.executable))))
        spec["read"].append(sys.base_prefix)
        failures = 0
        for label, shell, expect_ok in probes:
            code, out = _probe(spec, shell)
            ok = (code == 0) == expect_ok
            failures += not ok
            verdict = "PASS" if ok else "FAIL"
            want = "allowed" if expect_ok else "denied"
            print(f"  [{verdict}] {label}: {want} (exit {code})" + ("" if ok else f" — {out[:200]}"))
        print("✓ Sandbox enforcement verified." if not failures else f"✗ {failures} probe(s) failed.")
        return 1 if failures else 0


def _run(args) -> int:
    """Run a command under this shell's session policy (debug grants without the agent)."""
    from hermes_security.sandbox.policy import SessionOverrides, build_policy, sandbox_config, session_tmp_dir
    from hermes_security.sandbox.session import current_session_key, load_overrides, session_workspace
    from hermes_security.sandbox.spawn import wrap_argv
    command = [c for c in args.run_command if c != "--"]
    if not command:
        print("Usage: hermes sandbox run [--preset P] [--grant PATH[:rw]] [--net] -- COMMAND ...")
        return 2
    if not _require_backend():
        return 1
    cfg = dict(sandbox_config(), enabled=True)
    key = current_session_key()
    overrides = load_overrides(key)
    overrides = SessionOverrides(**{**overrides.__dict__,
                                    "presets": [*overrides.presets, *(args.preset or [])],
                                    "grants": [*overrides.grants, *(args.grant or [])],
                                    "network": True if args.net else overrides.network})
    policy = build_policy(cfg, overrides, workspace=session_workspace(), tmp_dir=session_tmp_dir(key))
    from hermes_security.sandbox.spawn import _VAULT_CREDENTIALS, apply_env
    env = apply_env(dict(os.environ), {"TMPDIR": policy.tmp_dir, **dict.fromkeys(_VAULT_CREDENTIALS)})
    if args.explain:
        print(json.dumps(policy.launch_spec(), indent=2))
    return subprocess.call(wrap_argv(command, policy.launch_spec()), env=env)


def _setup(args) -> int:
    """Guided setup: backend check → default presets → enable → live self-check."""
    from hermes_cli.curses_ui import curses_checklist
    from hermes_security.sandbox.policy import BUILTIN_PRESETS
    from hermes_security.sandbox.spawn import backend_status
    status = backend_status()
    print(f"OS sandbox backend: {status['backend']} — {status['detail']}")
    if not status["available"]:
        return 1
    names = list(BUILTIN_PRESETS)
    _, section = _load()
    current = set(section.get("default_presets") or ["coding"])
    chosen = curses_checklist("Presets applied to every new session (space toggles, enter confirms)",
                              [f"{n:13} {BUILTIN_PRESETS[n].description}" for n in names],
                              {i for i, n in enumerate(names) if n in current})
    args.preset = [names[i] for i in sorted(chosen)]
    code = _enable(args)
    return code or _check(args)


_ACTIONS = {
    "status": _status, "enable": _enable, "disable": _disable, "grant": _grant, "revoke": _revoke,
    "tools": _list_edit("tools", "Allowed tools"), "preset": _list_edit("default_presets", "Default presets",
                                                                           _validate_presets),
    "presets": _presets, "scan": _scan, "check": _check, "run": _run, "setup": _setup,
}


def cmd_sandbox(args) -> int:
    return _ACTIONS[args.sandbox_command or "status"](args)
