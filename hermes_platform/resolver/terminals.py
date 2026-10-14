"""Passive discovery and native launch commands for Linux/macOS terminal windows."""

from __future__ import annotations

import shlex

from hermes_platform.resolver import locate_command

_LINUX = {
    "gnome-terminal": ("--window", "--"), "konsole": ("--separate", "-e"),
    "kitty": (), "alacritty": ("-e",), "ghostty": ("-e",),
    "wezterm": ("start", "--always-new-process", "--"),
    "x-terminal-emulator": ("-e",), "xterm": ("-e",),
    "xfce4-terminal": ("--disable-server", "--execute"),
}


def terminal_command(terminal: str, command: list[str], *, platform: str) -> list[str]:
    if platform == "darwin" and terminal == "Terminal":
        # AppleScript has its own string escaping, in addition to the shell's argv quoting.
        shell = "exec " + shlex.join(command)
        escaped = shell.replace("\\", "\\\\").replace('"', '\\"')
        return ["osascript", "-e", f'tell application "Terminal"\nactivate\ndo script "{escaped}"\nend tell']
    if terminal == "tmux":
        return ["tmux", "new-session", "-d", shlex.join(command)]
    if platform == "linux" and terminal in _LINUX:
        return [terminal, *_LINUX[terminal], *command]
    raise ValueError(f"Unsupported terminal {terminal!r} on {platform}. Use Linux or macOS terminals.")


def resolve_terminal(requested: str, *, platform: str) -> tuple[str, str]:
    names = (["Terminal"] if platform == "darwin" else list(_LINUX)) if requested == "auto" else [requested]
    for name in names:
        # Validate the name before lookup; lookup never starts or installs a terminal.
        terminal_command(name, ["true"], platform=platform)
        found = locate_command("osascript" if name == "Terminal" else name)
        if found.found:
            return name, found.command[0]
    raise FileNotFoundError("No terminal found. Install a Linux terminal (also inside WSL), or use --terminal tmux.")
