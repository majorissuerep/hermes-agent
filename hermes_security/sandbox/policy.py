"""Sandbox policy model: grants, presets, and the effective per-session policy.

A grant is ``PATH[:ro|:rw]`` (default ``ro``). ``ro`` = read + execute beneath the path,
``rw`` = every filesystem right beneath it. Two dynamic tokens expand at spawn time:

- ``@cwd``  — the session's workspace (``TERMINAL_CWD`` for the session, else the
  process cwd);
- ``@path`` — the toolchain directories on ``PATH`` (plus the install root of each
  ``<root>/bin`` outside ``$HOME`` and ``~/.local``).

The effective policy is: ``sandbox:`` in config.yaml (``grants``, ``default_presets``,
``tools``, ``network``, ``unix_sockets``) + the session's own presets/grants/tool
additions (:mod:`hermes_security.sandbox.session`) − the session's revocations. Nothing
is granted unless one of those names it; the only implicit access is the read-only OS
baseline every program needs to start (``/usr``, ``/etc``, loader paths, ``/proc``, a few
devices) and a private per-session temp dir.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional

MODES = ("ro", "rw")
SUBAGENT_MODES = ("inherit", "readonly")


class SandboxError(ValueError):
    """A malformed grant, preset or sandbox setting."""


@dataclass(frozen=True)
class Grant:
    path: str
    mode: str = "ro"

    @property
    def spec(self) -> str:
        return f"{self.path}:{self.mode}"


def parse_grant(spec: str) -> Grant:
    """``"~/proj:rw"`` → ``Grant("~/proj", "rw")``; a bare path is read-only."""
    text = str(spec or "").strip()
    if not text:
        raise SandboxError("empty grant")
    path, sep, mode = text.rpartition(":")
    if sep and mode in MODES and path:
        return Grant(path, mode)
    return Grant(text, "ro")


@dataclass(frozen=True)
class Preset:
    description: str
    grants: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    network: Optional[bool] = None
    include: tuple[str, ...] = ()


BUILTIN_PRESETS: Mapping[str, Preset] = {
    "workspace": Preset("read-write access to the session's working directory", grants=("@cwd:rw",)),
    "workspace-ro": Preset("read-only access to the session's working directory", grants=("@cwd:ro",)),
    "toolchains": Preset("read/execute the toolchains on PATH (compilers, runtimes, CLIs)", grants=("@path:ro",)),
    "shell-rc": Preset("read the shell startup files (aliases, PATH setup)",
                       grants=("~/.profile", "~/.bashrc", "~/.bash_profile", "~/.bash_aliases",
                               "~/.zshenv", "~/.zprofile", "~/.zshrc", "~/.inputrc")),
    "git": Preset("read git configuration", grants=("~/.gitconfig", "~/.config/git")),
    "network": Preset("allow sandboxed processes to open internet connections", network=True),
    "files": Preset("file tools only (read/write/patch/search) — pair with a workspace preset",
                    tools=("file",)),
    "coding": Preset("workspace + toolchains + shell rc + git; terminal, file, code and planning tools",
                     include=("workspace", "toolchains", "shell-rc", "git"),
                     tools=("terminal", "file", "code_execution", "todo", "clarify")),
    "research": Preset("web search/extract, planning and clarifying questions; no files",
                       tools=("web", "todo", "clarify")),
}

# Read-only OS baseline: what any dynamically linked program needs to start. It contains
# no user data. /etc/shadow & co stay protected by their own permissions.
_BASELINE_RO = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32", "/etc", "/opt",
                "/nix", "/run/current-system", "/proc", "/sys",
                "/dev/random", "/dev/urandom")
_BASELINE_RW = ("/dev/null", "/dev/zero", "/dev/full")
_MACOS_BASELINE_RO = ("/usr", "/bin", "/sbin", "/System", "/Library", "/private/etc",
                      "/private/var/db/timezone", "/opt/homebrew", "/opt/local", "/nix",
                      "/dev/random", "/dev/urandom", "/Applications/Xcode.app")


@dataclass(frozen=True)
class SandboxPolicy:
    enabled: bool
    grants: tuple[Grant, ...] = ()
    tools: frozenset[str] = frozenset()
    network: bool = False
    unix_sockets: bool = False
    subagents: str = "inherit"
    presets: tuple[str, ...] = ()
    workspace: str = ""
    tmp_dir: str = ""
    scan_on_grant: bool = True
    revoked: frozenset[str] = frozenset()

    def expanded_grants(self) -> list[Grant]:
        """Grants with ``~``/``@cwd``/``@path`` resolved to absolute paths, de-duplicated
        (``rw`` wins over ``ro`` for the same path)."""
        best: dict[str, str] = {}
        for grant in self.grants:
            for path in expand_path(grant.path, workspace=self.workspace):
                if path in self.revoked:
                    continue
                if best.get(path) != "rw":
                    best[path] = grant.mode
        return [Grant(p, m) for p, m in best.items()]

    def launch_spec(self, *, subagent: bool = False,
                    extra_read: Iterable[str] = (), extra_write: Iterable[str] = ()) -> dict:
        """The OS-neutral confinement request handed to the platform backend."""
        downgrade = subagent and self.subagents == "readonly"
        read: list[str] = list(_MACOS_BASELINE_RO if _os_family() == "darwin" else _BASELINE_RO)
        write: list[str] = list(_BASELINE_RW)
        protected = protected_paths()
        for grant in self.expanded_grants():
            target = write if grant.mode == "rw" and not downgrade else read
            target.extend(carve_out(grant.path, protected))
        read.extend(extra_read)
        write.extend(extra_write)
        if self.tmp_dir:
            write.append(self.tmp_dir)
        return {"read": _dedupe(read), "write": _dedupe(write),
                "network": self.network, "unix_sockets": self.unix_sockets}


def _os_family() -> str:
    from hermes_platform.host.facts import os_family
    return os_family()


def protected_paths() -> set[str]:
    """Never reachable through a grant: the Hermes root and active home (vault, config — the
    sandbox's own policy — sessions, credentials) and every session's private temp dir.
    A grant that COVERS one of them is carved around it (see :func:`carve_out`)."""
    from hermes_constants import get_default_hermes_root, get_hermes_home
    roots = {get_default_hermes_root(), get_hermes_home(), _tmp_root()}
    return {os.path.realpath(p) for p in roots}


def _is_within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def carve_out(path: str, protected: set[str]) -> list[str]:
    """``path`` as grantable paths that exclude every protected path. Landlock and
    Seatbelt rules both cover whole subtrees and Landlock cannot subtract, so a grant on an
    ANCESTOR of a protected path becomes grants on the ancestor's other children
    (recursively along the way down). Costs: no new top-level entries in that ancestor."""
    real = os.path.realpath(path)
    if any(_is_within(real, p) for p in protected):
        return []
    inside = [p for p in protected if _is_within(p, real)]
    if not inside:
        return [path]
    try:
        names = sorted(os.listdir(real))
    except OSError:
        return []
    out: list[str] = []
    for name in names:
        out.extend(carve_out(os.path.join(real, name), protected))
    return out


def _tmp_root() -> Path:
    return Path(tempfile.gettempdir()) / f"hermes-sandbox-{os.getuid()}"


def _dedupe(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def toolchain_dirs(path_env: Optional[str] = None) -> list[str]:
    """``@path``: every existing PATH directory, plus the install root of a ``<root>/bin``
    (``~/.cargo``, ``~/go``, ``~/.nvm/versions/node/vX``) so runtimes find their libraries.
    Never the home dir itself, ``~/.local`` (it holds app data), the Hermes home, or a
    Windows drive mount (WSL interop runs binaries outside any Linux sandbox)."""
    from hermes_constants import get_hermes_home
    home = _home().resolve()
    never = {home, home / ".local", Path(get_hermes_home()).resolve(), Path("/")}
    out: list[str] = []
    raw = os.environ.get("PATH", "") if path_env is None else path_env
    for entry in raw.split(os.pathsep):
        if not entry or entry.startswith("/mnt/"):
            continue
        try:
            directory = Path(entry).resolve()
        except OSError:
            continue
        if not directory.is_dir() or directory in never:
            continue
        out.append(str(directory))
        if directory.name == "bin" and directory.parent not in never and home in directory.parents:
            out.append(str(directory.parent))
    return _dedupe(out)


def expand_path(raw: str, *, workspace: str = "") -> list[str]:
    """Absolute path(s) a grant path names; ``[]`` when it names nothing."""
    text = str(raw).strip()
    if text == "@path":
        return toolchain_dirs()
    if text == "@cwd" or text.startswith("@cwd/"):
        base = os.path.realpath(workspace or os.getcwd())
        if text == "@cwd" and base in (str(_home().resolve()), "/"):
            return []  # a "workspace" preset means a project, never the whole home or root
        text = base + text[len("@cwd"):]
    return [os.path.abspath(os.path.expanduser(text))]


def resolve_presets(names: Iterable[str], custom: Mapping[str, Mapping] | None = None) -> list[tuple[str, Preset]]:
    """Presets by name with ``include`` flattened (depth-first, each at most once)."""
    catalog = dict(BUILTIN_PRESETS)
    for name, body in (custom or {}).items():
        if not isinstance(body, Mapping):
            raise SandboxError(f"sandbox preset {name!r} must be a mapping")
        net = body.get("network")
        catalog[str(name)] = Preset(
            description=str(body.get("description") or "custom preset"),
            grants=tuple(str(g) for g in body.get("grants") or ()),
            tools=tuple(str(t) for t in body.get("tools") or ()),
            network=None if net is None else bool(net),
            include=tuple(str(i) for i in body.get("include") or ()))
    seen: dict[str, Preset] = {}

    def visit(name: str, stack: tuple[str, ...]) -> None:
        if name in seen:
            return
        if name in stack:
            raise SandboxError(f"sandbox preset include cycle: {' -> '.join(stack + (name,))}")
        preset = catalog.get(name)
        if preset is None:
            raise SandboxError(f"unknown sandbox preset {name!r} (known: {', '.join(sorted(catalog))})")
        for child in preset.include:
            visit(child, stack + (name,))
        seen[name] = preset

    for name in names:
        visit(str(name), ())
    return list(seen.items())


def preset_catalog(custom: Mapping[str, Mapping] | None = None) -> dict[str, Preset]:
    return dict(resolve_presets(list(BUILTIN_PRESETS) + list(custom or {}), custom))


def session_tmp_dir(session_key: str) -> str:
    """A private 0700 temp dir per session: TMPDIR inside the sandbox, never shared."""
    root = _tmp_root()
    digest = hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:20]
    path = root / digest
    for directory in (root, path):
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.stat().st_uid != os.getuid():
            raise SandboxError(f"sandbox temp dir {directory} is owned by another user")
        os.chmod(directory, 0o700)
    return str(path)


def sandbox_config() -> dict:
    """The ``sandbox:`` section over its defaults. Read on every spawn and tool call, so it
    uses the signature-cached raw reader, which never initializes a Hermes home."""
    from hermes_cli.config import read_raw_config_readonly
    from hermes_cli.config_defaults import DEFAULT_CONFIG
    from hermes_constants import get_hermes_home
    home = str(get_hermes_home())
    raw = read_raw_config_readonly() or {}
    if not raw and home in _LAST_ENABLED:
        # config.yaml vanished or became unreadable after this process saw the sandbox on:
        # stay confined (an explicit `enabled: false` is the only way off).
        return _LAST_ENABLED[home]
    section = raw.get("sandbox")
    merged = {**DEFAULT_CONFIG["sandbox"], **(section if isinstance(section, dict) else {})}
    if merged.get("enabled"):
        _LAST_ENABLED[home] = merged
    else:
        _LAST_ENABLED.pop(home, None)
    return merged


_LAST_ENABLED: dict[str, dict] = {}


def is_enabled() -> bool:
    return bool(sandbox_config().get("enabled"))


@dataclass
class SessionOverrides:
    """What a session added on top of config (see :mod:`hermes_security.sandbox.session`)."""
    grants: list[str] = field(default_factory=list)
    revoked: list[str] = field(default_factory=list)
    presets: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    tools_removed: list[str] = field(default_factory=list)
    network: Optional[bool] = None
    unix_sockets: Optional[bool] = None


def build_policy(config: Mapping, overrides: SessionOverrides, *, workspace: str = "",
                 tmp_dir: str = "") -> SandboxPolicy:
    """Pure: config section + session overrides → effective policy."""
    custom = config.get("presets") or {}
    preset_names = [*(config.get("default_presets") or ()), *overrides.presets]
    presets = resolve_presets(preset_names, custom)
    grants: list[Grant] = []
    tools: set[str] = {str(t) for t in config.get("tools") or ()}
    network = bool(config.get("network", False))
    for _, preset in presets:
        grants.extend(parse_grant(g) for g in preset.grants)
        tools.update(preset.tools)
        if preset.network is not None:
            network = preset.network
    grants.extend(parse_grant(g) for g in config.get("grants") or ())
    grants.extend(parse_grant(g) for g in overrides.grants)
    tools.update(overrides.tools)
    tools.difference_update(overrides.tools_removed)
    if overrides.network is not None:
        network = overrides.network
    revoked = frozenset(p for r in overrides.revoked for p in expand_path(r, workspace=workspace))
    subagents = str(config.get("subagents") or "inherit")
    if subagents not in SUBAGENT_MODES:
        raise SandboxError(f"sandbox.subagents must be one of {SUBAGENT_MODES}, got {subagents!r}")
    unix = bool(config.get("unix_sockets", False))
    if overrides.unix_sockets is not None:
        unix = overrides.unix_sockets
    return SandboxPolicy(
        enabled=bool(config.get("enabled")), grants=tuple(grants), tools=frozenset(tools),
        network=network, unix_sockets=unix, subagents=subagents,
        presets=tuple(name for name, _ in presets), workspace=workspace, tmp_dir=tmp_dir,
        scan_on_grant=bool(config.get("scan_on_grant", True)), revoked=revoked)
