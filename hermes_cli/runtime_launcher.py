"""Credential-free commands bound to the running installation's source."""
from __future__ import annotations

from functools import cache
from pathlib import Path
import shlex
from tempfile import TemporaryDirectory


def write_cli(directory: Path, source: Path) -> Path:
    """Publish a private shell command; bootstrap selects dependencies at launch."""
    from hermes_cli._launchers import runtime_command

    command = directory / "hermes"
    command.write_text('#!/bin/sh\nexec ' + shlex.join(runtime_command(source)) + ' "$@"\n', encoding="utf-8")
    command.chmod(0o500)
    return directory


@cache
def _temporary_cli(source: Path) -> TemporaryDirectory:
    directory = TemporaryDirectory(prefix="hermes-owned-cli-")
    write_cli(Path(directory.name), source)
    return directory


def owning_cli_dir(source: Path) -> Path:
    """Keep the temporary command alive for this process, then remove it at exit."""
    return Path(_temporary_cli(source.resolve()).name)
