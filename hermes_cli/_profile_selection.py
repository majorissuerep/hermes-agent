"""Read-only canonical CLI profile argument and home selection before boot writes."""
from __future__ import annotations

import os
from pathlib import Path
import sys

from hermes_constants import PROFILE_ID_RE

def _inside_mcp_add_args(argv: list, index: int) -> bool:
    """True once argv reaches `hermes mcp add ... --args <command argv>`.

    ``mcp add --args`` is command-argv passthrough. Flags after that point
    belong to the child MCP command (for example Docker MCP Toolkit's
    ``--profile``), not to Hermes' own profile selector.
    """
    try:
        mcp_index = argv.index("mcp", 0, index)
        argv.index("add", mcp_index + 1, index)
    except ValueError:
        return False
    return True

def _looks_like_hermes_invocation() -> bool:
    """False when ``sys.argv`` belongs to a test runner rather than a ``hermes`` run.

    pytest's own ``-p no:xdist`` reaches ``_scan_profile_flag`` through ``sys.argv`` at import
    time; it must stay a silent skip, while a real ``hermes -p 'Work Bot'`` must fail loudly.
    """
    return "pytest" not in (sys.argv[0] or "")

def _looks_like_option_value(value: str) -> bool:
    """A ``-p`` value that clearly belongs to some other tool (pytest's ``-p no:xdist``, a
    third-party ``-p --flag``), never a mistyped profile name."""
    return value.startswith("-") or ":" in value

def scan_profile_flag(argv: list, *, invalid_name=None) -> tuple:
    """Find -p/--profile/--profile= in argv -> (name, tokens_consumed, index).

    Historically the flag worked even after the subcommand (`hermes chat -p
    coder`), so scan broadly; stop at ``--`` and at the `mcp add --args`
    passthrough region. The value is normalised (strip + casefold, matching
    ``profiles.normalize_profile_name``) before validation so ``-p Work`` selects
    ``work``. A value that cannot be a profile name is rejected so
    resolve_profile_env never sys.exits on it; the rejection is explained (exit 2)
    only when the flag comes BEFORE the first subcommand token under a real
    ``hermes`` run — after a subcommand, ``-p`` may belong to that subcommand or a
    plugin (`hermes kanban ... -p 8080`), and option-looking values (``no:xdist``,
    ``--flag``) are always a silent skip.
    """
    from hermes_cli._parser import top_level_value_flag_sets

    value_flags, optional_value_flags = top_level_value_flag_sets()
    i = 0
    saw_subcommand = False
    while i < len(argv):
        arg = argv[i]
        if arg == "--" or (arg == "--args" and _inside_mcp_add_args(argv, i)):
            break
        if arg in {"--profile", "-p"} and i + 1 < len(argv):
            raw = argv[i + 1]
            value = raw.strip().casefold()
            if PROFILE_ID_RE.fullmatch(value):
                return value, 2, i
            if not saw_subcommand and not _looks_like_option_value(raw) and _looks_like_hermes_invocation():
                if invalid_name is None:
                    raise ValueError("Invalid profile name: " + raw)
                invalid_name(raw)
            break
        if arg.startswith("--profile="):
            return arg.split("=", 1)[1].strip().casefold(), 1, i
        takes_value = "=" not in arg and i + 1 < len(argv) and (
            arg in value_flags
            or (arg in optional_value_flags and not argv[i + 1].startswith("-"))
        )
        if not takes_value and not arg.startswith("-"):
            saw_subcommand = True
        i += 2 if takes_value else 1
    return None, 0, None

def sudo_user_profile_env(name: str) -> str | None:
    """Resolve `sudo hermes -p <name>` against the invoking user's home.

    This runs before argparse, so `--run-as-user` is not available yet. For
    sudo invocations the best signal is SUDO_USER: root is only doing the
    privileged install/start action; the profile store belongs to the user.
    """
    if name == "default":
        return None
    from hermes_constants import named_profile_is_live, sudo_invoker_default_home

    sudo_home = sudo_invoker_default_home()
    if sudo_home is None:
        return None
    candidate = sudo_home / "profiles" / name
    return str(candidate) if named_profile_is_live(candidate) else None


def select_profile(argv: list[str], *, invalid_name=None) -> tuple:
    """Selected name, consumed flag/index, and whether selection came from active_profile."""
    name, consume, index = scan_profile_flag(argv, invalid_name=invalid_name)
    home = os.environ.get("HERMES_HOME", "")
    if name is None and home and Path(home).parent.name == "profiles":
        return None, 0, None, False
    sticky = False
    if name is None:
        from hermes_constants import get_default_hermes_root
        try:
            saved = (get_default_hermes_root() / "active_profile").read_text(encoding="utf-8-sig").strip()
            if saved and saved != "default":
                name, sticky = saved, True
        except (OSError, UnicodeDecodeError):
            pass
    return name, consume, index, sticky

def profile_root_for_env_home(env_home: str, default_root: Path) -> Path:
    """Canonical lexical root; preserve configured aliases and named-profile re-homing."""
    raw = env_home.strip()
    if not raw:
        return default_root
    home = Path(raw)
    return home.parent.parent if home.parent.name == "profiles" else home

def profile_home(name: str, *, default_root: Path | None = None) -> Path | None:
    """Resolve metadata only; no config, crypto, log, directory or default-store writes."""
    from hermes_constants import get_default_hermes_root, named_profile_is_live
    canon = name.strip().lower()
    if not PROFILE_ID_RE.fullmatch(canon):
        raise ValueError("Invalid profile name: " + name)
    root = profile_root_for_env_home(os.environ.get("HERMES_HOME", ""),
                                     get_default_hermes_root() if default_root is None else default_root)
    if canon == "default":
        return root
    selected = root / "profiles" / canon
    return selected if named_profile_is_live(selected) else None
